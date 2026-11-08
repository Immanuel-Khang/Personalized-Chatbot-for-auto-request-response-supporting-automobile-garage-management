"""[TRACK C] Admin API. Đăng nhập bằng bảng managers (app/auth.py; tạo tài khoản: python -m app.manage).
Vai manager xem được mọi cuộc chat (kể cả bot tự xử lý); vai khác chỉ thấy chat đã có ticket chuyển người.
Luồng chuyển giao: OPEN (bot im lặng) -> claim (HUMAN_ACTIVE, báo khách) -> reply -> resolve (trả về bot, báo khách).
Nhân viên cũng có thể chủ động tiếp nhận một chat bot đang xử lý (takeover), vd từ trang Lịch hẹn.
Lịch hẹn do bot tự đặt/đổi/hủy. Nhân viên XÁC NHẬN (gán khung giờ, báo khách), sau khi làm xong đánh dấu HOÀN THÀNH;
nhân viên cũng tự HỦY / ĐỔI lịch được (khách được báo trong chat).
Đăng nhập sai liên tiếp ADMIN_MAX_LOGIN_ATTEMPTS lần thì khóa tài khoản ADMIN_LOCK_MINUTES phút."""
import math
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth import AdminUser, admin_user, hash_password, make_token, verify_password
from app.config import settings
from app.contracts.schemas import ConversationMode
from app.conversation import _locks, post_system
from app.db.models import Appointment, ChatSession, Customer, HandoverTicket, Manager, Message
from app.db.session import SessionLocal
from app.services.booking import (DETAIL_PREFIX, LOCAL_TZ, VEHICLE_PREFIX, _note_value, complete_appointment,
                                  confirm_appointment, default_booking_config, get_booking_config, set_booking_config,
                                  slot_overview, staff_cancel_appointment, staff_reschedule_appointment)
from app.services.slots import (MAX_CAPACITY, MAX_SLOT_MINUTES, MIN_CAPACITY, MIN_SLOT_MINUTES, BookingConfig, parse_hhmm,
                                validate)
from app.services.handover import open_ticket

router = APIRouter(prefix="/admin")

CLAIMED_NOTICE = "Nhân viên {assignee} đã tiếp nhận cuộc trò chuyện và sẽ hỗ trợ bạn trực tiếp từ bây giờ."
RESOLVED_NOTICE = "Nhân viên đã hoàn tất hỗ trợ. Trợ lý tự động sẽ tiếp tục đồng hành cùng bạn, cần gì bạn cứ nhắn nhé."


class StaffReply(BaseModel):
    content: str


class Login(BaseModel):
    username: str
    password: str


_dummy_hash: str | None = None


@router.post("/login")
def login(body: Login):
    if not settings.admin_auth_required:
        return {"token": "", "username": None, "role": "manager"}
    global _dummy_hash
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        manager = db.scalars(select(Manager).where(Manager.username == body.username.strip()).with_for_update()).one_or_none()
        if manager is not None and manager.locked_until is not None:
            if manager.locked_until > now:  # đang bị khóa: không kiểm tra mật khẩu, kể cả nhập đúng
                minutes = max(1, math.ceil((manager.locked_until - now).total_seconds() / 60))
                raise HTTPException(429, f"Tài khoản đang bị khóa tạm thời do nhập sai quá nhiều lần. Thử lại sau {minutes} phút.")
            manager.locked_until, manager.failed_logins = None, 0  # hết hạn khóa
        if manager is None:  # tài khoản không tồn tại: vẫn băm mật khẩu để thời gian phản hồi không lộ ra điều đó
            _dummy_hash = _dummy_hash or hash_password("khong-ton-tai")
            verify_password(body.password, _dummy_hash)
        elif manager.is_active and verify_password(body.password, manager.password_hash):
            manager.failed_logins, manager.locked_until = 0, None
            db.commit()
            user = AdminUser(id=manager.id, username=manager.username, role=manager.role)
            return {"token": make_token(user), "username": user.username, "role": user.role}
        else:
            manager.failed_logins += 1
            if manager.failed_logins >= settings.admin_max_login_attempts:
                manager.locked_until = now + timedelta(minutes=settings.admin_lock_minutes)
                manager.failed_logins = 0
                db.commit()
                raise HTTPException(429, f"Nhập sai {settings.admin_max_login_attempts} lần. Tài khoản bị khóa {settings.admin_lock_minutes} phút.")
            db.commit()
        raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")


def _staff_of(db, user: AdminUser, fallback_username: str | None = None) -> Manager | None:
    """Nhân viên thực hiện thao tác: người đang đăng nhập (khi tắt xác thực dùng ?manager=)."""
    return db.get(Manager, user.id) if user.id else _get_manager(db, fallback_username)


def _can_read(db, user: AdminUser, session_id: str) -> bool:
    """manager đọc mọi phiên; vai khác chỉ đọc phiên đã có ticket chuyển người."""
    return user.is_manager or db.query(HandoverTicket.id).filter_by(session_id=session_id).first() is not None


def _get_ticket(db, ticket_id: int) -> HandoverTicket:
    ticket = db.get(HandoverTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    return ticket


def _get_manager(db, username: str | None) -> Manager | None:
    if username is None:
        return None
    manager = db.query(Manager).filter_by(username=username, is_active=True).first()
    if not manager:
        raise HTTPException(404, "manager not found")
    return manager


# Trạng thái hội thoại = trạng thái ticket mới nhất của phiên; phiên chưa từng chuyển người là "BOT".
CONVERSATION_STATUSES = ("BOT", "OPEN", "CLAIMED", "RESOLVED")


@router.get("/conversations")
def list_conversations(status: str = "ALL", q: str = "", limit: int = Query(200, ge=1, le=500),
                       user: AdminUser = Depends(admin_user)):
    """Mọi cuộc trò chuyện (kể cả chỉ bot trả lời), mới nhất trước.
    status: ALL | BOT (bot đang xử lý) | OPEN (chờ nhận) | CLAIMED (đang xử lý) | RESOLVED (đã xong).
    q: tìm theo mã phiên, tên/SĐT khách, tóm tắt ticket hoặc nội dung tin nhắn. counts luôn tính trên toàn bộ phiên."""
    status = status.upper()
    if status != "ALL" and status not in CONVERSATION_STATUSES:
        raise HTTPException(422, f"status must be ALL or one of {CONVERSATION_STATUSES}")
    if status == "BOT" and not user.is_manager:
        raise HTTPException(403, "Chỉ quản lý được xem các cuộc trò chuyện do trợ lý tự xử lý")
    with SessionLocal() as db:
        latest = (select(HandoverTicket.session_id, func.max(HandoverTicket.id).label("tid"))
                  .group_by(HandoverTicket.session_id).subquery())
        rows = db.execute(
            select(ChatSession, HandoverTicket, Customer)
            .outerjoin(latest, latest.c.session_id == ChatSession.id)
            .outerjoin(HandoverTicket, HandoverTicket.id == latest.c.tid)
            .outerjoin(Customer, Customer.id == ChatSession.customer_id)
            .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc())).all()
        if not user.is_manager:  # vai khác chỉ thấy phiên đã chuyển người
            rows = [r for r in rows if r[1] is not None]

        counts = {"ALL": len(rows), **{k: 0 for k in CONVERSATION_STATUSES}}
        for _, ticket, _ in rows:
            counts[ticket.status if ticket else "BOT"] += 1

        needle = q.strip()
        if needle:
            like = f"%{needle}%"
            by_text = {sid for (sid,) in db.execute(
                select(Message.session_id).where(Message.role != "tool", Message.content.ilike(like)).distinct())}
        picked = []
        for session, ticket, customer in rows:
            state = ticket.status if ticket else "BOT"
            if status != "ALL" and state != status:
                continue
            if needle and not (session.id in by_text
                               or any(needle.lower() in (v or "").lower() for v in (
                                   session.id, customer and customer.full_name, customer and customer.phone_number,
                                   ticket and ticket.summary))):
                continue
            picked.append((session, ticket, customer, state))
        picked = picked[:limit]

        ids = [p[0].id for p in picked]
        last, total = {}, {}
        if ids:
            newest = (select(func.max(Message.id)).where(Message.session_id.in_(ids), Message.role != "tool")
                      .group_by(Message.session_id))
            last = {m.session_id: m for m in db.execute(select(Message).where(Message.id.in_(newest))).scalars()}
            total = dict(db.execute(select(Message.session_id, func.count()).where(
                Message.session_id.in_(ids), Message.role != "tool").group_by(Message.session_id)).all())
        managers = {m.id: m.username for m in db.query(Manager).all()}

        items = []
        for session, ticket, customer, state in picked:
            m = last.get(session.id)
            items.append({
                "session_id": session.id, "status": state, "mode": session.mode,
                "customer_name": customer.full_name if customer else None,
                "customer_phone": customer.phone_number if customer else None,
                "ticket_id": ticket.id if ticket else None, "reason": ticket.reason if ticket else None,
                "summary": ticket.summary if ticket else None, "sla_due_at": ticket.sla_due_at if ticket else None,
                "assigned": managers.get(ticket.assigned_manager_id) if ticket else None,
                "last_message_at": session.last_message_at or session.created_at,
                "last_message": {"role": m.role, "content": m.content} if m else None,
                "message_count": total.get(session.id, 0)})
        return {"counts": counts, "items": items, "username": user.username, "role": user.role}


@router.get("/conversations/{session_id}/messages")
def conversation_messages(session_id: str, after: int = 0, user: AdminUser = Depends(admin_user)):
    """Toàn bộ tin của phiên (khách, bot, nhân viên, hệ thống), cũ -> mới. after = id tin đã có (poll tăng dần)."""
    with SessionLocal() as db:
        if db.get(ChatSession, session_id) is None:
            raise HTTPException(404, "session not found")
        if not _can_read(db, user, session_id):
            raise HTTPException(403, "Chỉ quản lý được đọc cuộc trò chuyện do trợ lý tự xử lý")
        rows = (db.query(Message).filter(Message.session_id == session_id, Message.id > after, Message.role != "tool")
                .order_by(Message.id).all())
        return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at} for m in rows]


@router.get("/tasks")
def list_tasks(status: str = "OPEN", user: AdminUser = Depends(admin_user)):
    with SessionLocal() as db:
        rows = db.query(HandoverTicket).filter_by(status=status).order_by(HandoverTicket.created_at).all()
        return [{"id": t.id, "session_id": t.session_id, "reason": t.reason, "summary": t.summary,
                 "assigned_manager_id": t.assigned_manager_id, "sla_due_at": t.sla_due_at} for t in rows]


@router.get("/tasks/{ticket_id}/messages")
def task_messages(ticket_id: int, user: AdminUser = Depends(admin_user)):
    """Toàn bộ lịch sử phiên để nhân viên đọc ngữ cảnh trước/sau khi nhận."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        rows = db.query(Message).filter_by(session_id=ticket.session_id).order_by(Message.id).all()
        return [{"id": m.id, "role": m.role, "content": m.content} for m in rows]


@router.post("/tasks/{ticket_id}/claim")
def claim_task(ticket_id: int, manager: str | None = None, user: AdminUser = Depends(admin_user)):
    """Nhân viên (username trong managers) nhận ticket: HUMAN_PENDING -> HUMAN_ACTIVE, bot ngừng trả lời, khách được báo."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        staff = _staff_of(db, user, manager)
        if ticket.status != "OPEN":
            raise HTTPException(409, f"ticket is {ticket.status}")
        with _locks[ticket.session_id]:  # không chen giữa lúc bot đang trả lời lượt hiện tại
            ticket.status = "CLAIMED"
            ticket.assigned_manager_id = staff.id if staff else None
            db.get(ChatSession, ticket.session_id).mode = ConversationMode.HUMAN_ACTIVE.value
            post_system(db, ticket.session_id, CLAIMED_NOTICE.format(assignee=staff.username if staff else "tư vấn"))
            db.commit()
    return {"ok": True}


@router.post("/tasks/{ticket_id}/reply")
def staff_reply(ticket_id: int, body: StaffReply, user: AdminUser = Depends(admin_user)):
    """Nhân viên nhắn cho khách (chỉ khi đã nhận ticket)."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        if ticket.status != "CLAIMED":
            raise HTTPException(409, "claim the ticket before replying")
        msg = post_system(db, ticket.session_id, body.content, role="staff", manager_id=ticket.assigned_manager_id)
        db.commit()
        return {"ok": True, "message_id": msg.id}


@router.post("/tasks/{ticket_id}/resolve")
def resolve_task(ticket_id: int, user: AdminUser = Depends(admin_user)):
    """Đóng ticket, trả phiên về cho bot và báo khách."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        with _locks[ticket.session_id]:
            was_claimed = ticket.status == "CLAIMED"
            ticket.status, ticket.resolved_at = "RESOLVED", datetime.now(timezone.utc)
            db.get(ChatSession, ticket.session_id).mode = ConversationMode.BOT.value
            if was_claimed:  # ticket chưa ai nhận thì khách chưa từng nói chuyện với người -> không cần báo
                post_system(db, ticket.session_id, RESOLVED_NOTICE)
            db.commit()
    return {"ok": True}


@router.post("/conversations/{session_id}/takeover")
def takeover(session_id: str, user: AdminUser = Depends(admin_user)):
    """Nhân viên chủ động vào chat (vd để nhắn khách về lịch hẹn): tạo ticket đã nhận, bot im tới khi resolve.
    Phiên đang chờ nhận thì nhận luôn ticket đó; phiên do chính mình đang xử lý thì trả về ticket hiện có."""
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        staff = _staff_of(db, user)
        with _locks[session_id]:
            db.refresh(session)
            ticket = open_ticket(db, session_id)
            if ticket is not None and ticket.status == "CLAIMED":
                if ticket.assigned_manager_id not in (None, user.id):
                    raise HTTPException(409, "Nhân viên khác đang xử lý cuộc trò chuyện này")
                return {"ok": True, "ticket_id": ticket.id}
            if ticket is None:
                ticket = HandoverTicket(session_id=session_id, customer_id=session.customer_id, reason="OTHER",
                                        summary="[OTHER] Nhân viên chủ động tiếp nhận")
                db.add(ticket)
                db.flush()
            ticket.status, ticket.assigned_manager_id = "CLAIMED", staff.id if staff else None
            session.mode = ConversationMode.HUMAN_ACTIVE.value
            post_system(db, session_id, CLAIMED_NOTICE.format(assignee=staff.username if staff else "tư vấn"))
            db.commit()
            return {"ok": True, "ticket_id": ticket.id}


# Nhóm trạng thái lịch hẹn hiển thị trên trang Lịch hẹn.
APPOINTMENT_GROUPS = {"PENDING_APPROVAL": ("PENDING_APPROVAL",), "CONFIRMED": ("CONFIRMED",), "DONE": ("DONE",),
                      "CANCELLED": ("CANCELLED", "REJECTED")}


@router.get("/appointments")
def list_appointments(status: str = "ALL", q: str = "", limit: int = Query(300, ge=1, le=1000),
                      user: AdminUser = Depends(admin_user)):
    """Lịch hẹn do bot đặt/đổi/hủy, mới cập nhật trước. Nhân viên đổi/hủy qua POST /appointments/{id}/reschedule|cancel.
    status: ALL | PENDING_APPROVAL | CONFIRMED | DONE | CANCELLED (gồm REJECTED)."""
    status = status.upper()
    if status != "ALL" and status not in APPOINTMENT_GROUPS:
        raise HTTPException(422, f"status must be ALL or one of {tuple(APPOINTMENT_GROUPS)}")
    with SessionLocal() as db:
        latest = (select(HandoverTicket.session_id, func.max(HandoverTicket.id).label("tid"))
                  .group_by(HandoverTicket.session_id).subquery())
        rows = db.execute(
            select(Appointment, Customer, HandoverTicket.status)
            .join(Customer, Customer.id == Appointment.customer_id)
            .outerjoin(latest, latest.c.session_id == Appointment.session_id)
            .outerjoin(HandoverTicket, HandoverTicket.id == latest.c.tid)
            .order_by(Appointment.updated_at.desc(), Appointment.id.desc())).all()

        group_of = {s: g for g, members in APPOINTMENT_GROUPS.items() for s in members}
        counts = {"ALL": len(rows), **{g: 0 for g in APPOINTMENT_GROUPS}}
        for appt, _, _ in rows:
            counts[group_of[appt.status]] += 1

        needle = q.strip().lower()
        items = []
        for appt, customer, ticket_status in rows:
            if status != "ALL" and appt.status not in APPOINTMENT_GROUPS[status]:
                continue
            vehicle, detail = _note_value(appt.note, VEHICLE_PREFIX), _note_value(appt.note, DETAIL_PREFIX)
            if needle and needle not in " ".join(str(v or "") for v in (
                    appt.id, customer.full_name, appt.contact_phone, vehicle, detail, appt.note)).lower():
                continue
            items.append({
                "id": appt.id, "session_id": appt.session_id, "status": appt.status, "service_type": appt.service_type,
                "customer_name": customer.full_name, "phone": appt.contact_phone,
                "appointment_date": appt.appointment_date.isoformat() if appt.appointment_date else None,
                "start_time": appt.start_time.strftime("%H:%M") if appt.start_time else None,
                "vehicle": vehicle, "detail": detail, "created_at": appt.created_at, "updated_at": appt.updated_at,
                # trạng thái cuộc chat gắn với lịch (BOT nếu chưa từng chuyển người) để biết nhân viên đã vào chưa
                "conversation_status": (ticket_status or "BOT") if appt.session_id else None})
        return {"counts": counts, "items": items[:limit], "username": user.username, "role": user.role}


SERVICE_LABEL = {"MAINTENANCE": "Bảo dưỡng", "REPAIR": "Sửa chữa", "SHOWROOM_VISIT": "Xem xe tại showroom",
                 "CONTRACT_SIGN": "Ký hợp đồng"}


class ConfirmBody(BaseModel):
    start_time: str | None = None  # "HH:MM", chỉ cần khi khách chưa nêu giờ


@router.post("/appointments/{appointment_id}/confirm")
def confirm_appt(appointment_id: int, body: ConfirmBody | None = None, user: AdminUser = Depends(admin_user)):
    """Xác nhận lịch PENDING_APPROVAL: gán khung giờ rồi báo khách ngay trong chat. Bot không có quyền này."""
    when = None
    if body and body.start_time:
        try:
            when = time.fromisoformat(body.start_time.strip())
        except ValueError:
            raise HTTPException(422, "Giờ không hợp lệ, dùng định dạng HH:MM")
    result = confirm_appointment(appointment_id, when)
    if not result.ok:
        raise HTTPException({"NOT_FOUND": 404, "NEEDS_TIME": 422}.get(result.status, 409), result.message)
    with SessionLocal() as db:
        appt = db.get(Appointment, appointment_id)
        if appt is not None and appt.session_id:
            post_system(db, appt.session_id,
                        f"Lịch hẹn #{appt.id} ({SERVICE_LABEL.get(appt.service_type, appt.service_type)}) lúc "
                        f"{appt.start_time:%H:%M} ngày {appt.appointment_date:%d/%m/%Y} đã được nhân viên xác nhận. Hẹn gặp bạn!")
            db.commit()
    return {"ok": True, "status": "CONFIRMED"}


@router.post("/appointments/{appointment_id}/complete")
def complete_appt(appointment_id: int, user: AdminUser = Depends(admin_user)):
    """Chuyển lịch CONFIRMED sang DONE (đã làm xong), trả chỗ trong khung giờ và báo khách trong chat. Bot không có quyền này."""
    result = complete_appointment(appointment_id)
    if not result.ok:
        raise HTTPException(404 if result.status == "NOT_FOUND" else 409, result.message)
    with SessionLocal() as db:
        appt = db.get(Appointment, appointment_id)
        if appt is not None and appt.session_id:
            when = f" ngày {appt.appointment_date:%d/%m/%Y}" if appt.appointment_date else ""
            post_system(db, appt.session_id, f"Lịch hẹn #{appt.id} ({SERVICE_LABEL.get(appt.service_type, appt.service_type)}){when} "
                                             "đã hoàn thành. Cảm ơn bạn đã sử dụng dịch vụ của Garage One!")
            db.commit()
    return {"ok": True, "status": "DONE"}


# ---- Nhân viên hủy / đổi lịch (khách được báo trong chat) ---------------------------------------------------
class RescheduleBody(BaseModel):
    appointment_date: str  # "YYYY-MM-DD"
    start_time: str | None = None  # "HH:MM"; bỏ trống = chưa chọn giờ
    reason: str | None = None  # lý do, chèn vào thông báo cho khách


class CancelBody(BaseModel):
    reason: str | None = None


def _reason_text(reason: str | None) -> str:
    reason = " ".join((reason or "").split())[:200]
    return f" Lý do: {reason}." if reason else ""


def _appt_label(appt: Appointment) -> str:
    return f"Lịch hẹn #{appt.id} ({SERVICE_LABEL.get(appt.service_type, appt.service_type)})"


def parse_new_time(date_raw: str, time_raw: str | None) -> tuple[date, time | None]:
    """Ngày/giờ nhân viên nhập -> (date, time|None). ValueError (thông điệp tiếng Việt) nếu sai hoặc ngày đã qua."""
    try:
        day = date.fromisoformat((date_raw or "").strip())
    except ValueError:
        raise ValueError("Ngày không hợp lệ, dùng định dạng YYYY-MM-DD")
    if day < datetime.now(LOCAL_TZ).date():
        raise ValueError("Không đổi sang ngày đã qua")
    start = None
    if time_raw and time_raw.strip():
        try:
            start = time.fromisoformat(time_raw.strip())
        except ValueError:
            raise ValueError("Giờ không hợp lệ, dùng định dạng HH:MM")
    return day, start


def staff_cancel(appointment_id: int, reason: str | None = None):
    """Hủy lịch rồi báo khách trong chat. Trả BookingResult (ok=False nếu không hủy được, khi đó không báo khách)."""
    result = staff_cancel_appointment(appointment_id)
    if result.ok:
        with SessionLocal() as db:
            appt = db.get(Appointment, appointment_id)
            if appt is not None and appt.session_id:
                when = f" ngày {appt.appointment_date:%d/%m/%Y}" if appt.appointment_date else ""
                post_system(db, appt.session_id,
                            f"{_appt_label(appt)}{when} đã được nhân viên hủy.{_reason_text(reason)} "
                            "Nếu cần đặt lại, bạn cứ nhắn cho mình nhé.")
                db.commit()
    return result


def staff_reschedule(appointment_id: int, day: date, start: time | None, reason: str | None = None):
    """Đổi ngày giờ lịch rồi báo khách trong chat (nêu rõ lịch đã xác nhận hay còn chờ xác nhận)."""
    result = staff_reschedule_appointment(appointment_id, day, start)
    if result.ok:
        with SessionLocal() as db:
            appt = db.get(Appointment, appointment_id)
            if appt is not None and appt.session_id:
                at = f"{appt.start_time:%H:%M} " if appt.start_time else ""
                state = ("Lịch đã được xác nhận." if result.status == "CONFIRMED"
                         else "Lịch đang chờ nhân viên xác nhận lại.")
                post_system(db, appt.session_id,
                            f"{_appt_label(appt)} đã được nhân viên đổi sang {at}ngày {appt.appointment_date:%d/%m/%Y}."
                            f"{_reason_text(reason)} {state}")
                db.commit()
    return result


_APPT_ERRORS = {"NOT_FOUND": 404, "SLOT_FULL": 409, "DUPLICATE": 409}


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appt(appointment_id: int, body: CancelBody | None = None, user: AdminUser = Depends(admin_user)):
    """Nhân viên hủy lịch PENDING_APPROVAL/CONFIRMED, trả chỗ trong khung giờ và báo khách trong chat."""
    result = staff_cancel(appointment_id, body.reason if body else None)
    if not result.ok:
        raise HTTPException(_APPT_ERRORS.get(result.status, 409), result.message)
    return {"ok": True, "status": "CANCELLED"}


@router.post("/appointments/{appointment_id}/reschedule")
def reschedule_appt(appointment_id: int, body: RescheduleBody, user: AdminUser = Depends(admin_user)):
    """Nhân viên đổi ngày giờ lịch PENDING_APPROVAL/CONFIRMED (vẫn kiểm tra đầy chỗ/trùng) và báo khách trong chat."""
    try:
        day, start = parse_new_time(body.appointment_date, body.start_time)
    except ValueError as e:
        raise HTTPException(422, str(e))
    result = staff_reschedule(appointment_id, day, start, body.reason)
    if not result.ok:
        raise HTTPException(_APPT_ERRORS.get(result.status, 409), result.message)
    return {"ok": True, "status": result.status, "message": result.message}


# ---- Cấu hình khung giờ đặt lịch -------------------------------------------------------------------------
class BookingSettingsBody(BaseModel):
    slot_capacity: int | None = None  # số lịch tối đa trong một khung giờ
    slot_minutes: int | None = None   # độ dài khung giờ = khoảng cách giữa hai khung liên tiếp (phút)
    work_start: str | None = None     # "HH:MM", gốc của lưới khung giờ
    work_end: str | None = None


def _cfg_dict(cfg: BookingConfig) -> dict:
    return {"slot_capacity": cfg.slot_capacity, "slot_minutes": cfg.slot_minutes,
            "work_start": f"{cfg.work_start:%H:%M}", "work_end": f"{cfg.work_end:%H:%M}"}


@router.get("/settings/booking")
def get_booking_settings(user: AdminUser = Depends(admin_user)):
    """Cấu hình hiện hành (giá trị admin đã chỉnh, nếu chưa thì mặc định từ biến môi trường). Mọi vai đều xem được."""
    return {**_cfg_dict(get_booking_config()), "defaults": _cfg_dict(default_booking_config()), "can_edit": user.is_manager,
            "limits": {"capacity": [MIN_CAPACITY, MAX_CAPACITY], "slot_minutes": [MIN_SLOT_MINUTES, MAX_SLOT_MINUTES]}}


@router.put("/settings/booking")
def put_booking_settings(body: BookingSettingsBody, user: AdminUser = Depends(admin_user)):
    """Đổi sức chứa / độ dài khung giờ / giờ làm việc. Chỉ quản lý. Có hiệu lực cho lần đặt và xác nhận kế tiếp, lịch đã có giữ nguyên."""
    if not user.is_manager:
        raise HTTPException(403, "Chỉ quản lý được đổi cấu hình khung giờ")
    cur = get_booking_config()
    try:
        new = BookingConfig(
            slot_capacity=cur.slot_capacity if body.slot_capacity is None else body.slot_capacity,
            slot_minutes=cur.slot_minutes if body.slot_minutes is None else body.slot_minutes,
            work_start=cur.work_start if body.work_start is None else parse_hhmm(body.work_start),
            work_end=cur.work_end if body.work_end is None else parse_hhmm(body.work_end))
    except ValueError:
        raise HTTPException(422, "Giờ không hợp lệ, dùng định dạng HH:MM")
    if error := validate(new.slot_capacity, new.slot_minutes, new.work_start, new.work_end):
        raise HTTPException(422, error)
    return {**_cfg_dict(set_booking_config(new)), "can_edit": True}


@router.get("/slots")
def list_slots(day: str | None = Query(None, alias="date"), service: str = "SERVICE_BAY", user: AdminUser = Depends(admin_user)):
    """Tải từng khung giờ trong một ngày: đã xác nhận / đang chờ / còn trống. service: SERVICE_BAY | SHOWROOM_VISIT."""
    if service not in ("SERVICE_BAY", "SHOWROOM_VISIT"):
        raise HTTPException(422, "service phải là SERVICE_BAY hoặc SHOWROOM_VISIT")
    try:
        target = date.fromisoformat(day) if day else datetime.now(timezone(timedelta(hours=7))).date()
    except ValueError:
        raise HTTPException(422, "date phải có dạng YYYY-MM-DD")
    return slot_overview(target, service)
