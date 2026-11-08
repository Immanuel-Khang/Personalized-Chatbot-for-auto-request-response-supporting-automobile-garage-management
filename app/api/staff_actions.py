"""[TRACK C] Hộp thoại duyệt cập nhật từ tin nhắn nhân viên.
Luồng: nhân viên nhắn khách -> POST /tasks/{id}/actions/scan (bộ phân tích đề xuất) -> hộp thoại hiện đề xuất, nhân viên
sửa nếu cần -> POST /staff-actions/{id}/apply (thực thi thật) hoặc /dismiss. Chưa duyệt thì không có gì thay đổi.
Lịch hẹn ghi thẳng vào DB (bot đọc lại từ đó). Giá chốt, thông tin khách, ghi chú vào checkpoint của bot (known_facts)
và tóm tắt ticket; thông tin khách còn vào hồ sơ customers."""
import logging
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.runner import update_known_facts
from app.api.admin import _can_read, _get_ticket, staff_cancel, staff_reschedule
from app.auth import AdminUser, admin_user
from app.contracts.schemas import ServiceType
from app.conversation import _locks, _save_phone, post_system
from app.db.models import ChatSession, Customer, StaffAction
from app.db.session import SessionLocal
from app.identity import is_full_name, sync_contact
from app.services.booking import DbBookingService, confirm_appointment
from app.services.staff_actions import (NOTE_TOPICS, SERVICE_LABEL, analyze_ticket, context_for, describe, dismiss_pending, fmt_vnd,
                                        list_pending, normalize_params, serialize, unscanned_staff)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")
NOTE_LIMIT = 10  # số ghi chú xử lý gần nhất giữ trong known_facts (tránh phình prompt)


class ApplyBody(BaseModel):
    params: dict | None = None  # giá trị nhân viên đã sửa trong hộp thoại; bỏ trống = dùng đề xuất


def _guard(db, user: AdminUser, ticket_id: int):
    ticket = _get_ticket(db, ticket_id)
    if not _can_read(db, user, ticket.session_id):
        raise HTTPException(403, "Bạn không có quyền với cuộc trò chuyện này")
    return ticket


@router.post("/tasks/{ticket_id}/actions/scan")
def scan_actions(ticket_id: int, user: AdminUser = Depends(admin_user)):
    """Phân tích tin nhân viên mới của ticket, trả các đề xuất đang chờ duyệt (+ dữ liệu cho hộp thoại)."""
    with SessionLocal() as db:
        _guard(db, user, ticket_id)
    return {"actions": analyze_ticket(ticket_id), "unscanned_staff": unscanned_staff(ticket_id), **context_for(ticket_id)}


@router.get("/tasks/{ticket_id}/actions")
def pending_actions(ticket_id: int, user: AdminUser = Depends(admin_user)):
    """Đề xuất đang chờ duyệt, không phân tích lại (không tốn LLM)."""
    with SessionLocal() as db:
        _guard(db, user, ticket_id)
    return {"actions": list_pending(ticket_id), "unscanned_staff": unscanned_staff(ticket_id), **context_for(ticket_id)}


@router.post("/tasks/{ticket_id}/actions/dismiss-all")
def dismiss_all_actions(ticket_id: int, user: AdminUser = Depends(admin_user)):
    """Kết thúc hỗ trợ mà không duyệt: bỏ mọi đề xuất còn chờ để lần vào chat sau không phải duyệt lại."""
    with SessionLocal() as db:
        _guard(db, user, ticket_id)
    return {"ok": True, "dismissed": dismiss_pending(ticket_id, user.id or None)}


# ---- thực thi: mỗi hàm trả (ok, thông điệp) ------------------------------------------------------------------------
def _day(p: dict) -> date | None:
    return date.fromisoformat(p["date"]) if p.get("date") else None


def _time(p: dict) -> time | None:
    return time.fromisoformat(p["time"]) if p.get("time") else None


def _book(session_id: str, p: dict) -> tuple[bool, str]:
    if not (p.get("service_type") and p.get("date") and p.get("phone")):
        return False, "Thiếu loại dịch vụ, ngày hẹn hoặc số điện thoại"
    result = DbBookingService().create_appointment(session_id, ServiceType(p["service_type"]), _day(p), _time(p),
                                                   p["phone"], p.get("detail"))
    if not result.ok:
        alt = f" Giờ còn trống: {', '.join(result.alternatives)}." if result.alternatives else ""
        return False, result.message + alt
    status, text = result.status, "Đã đặt lịch, đang chờ xác nhận."
    if p.get("time"):  # nhân viên tự đặt kèm giờ cụ thể = đã duyệt luôn (như đổi lịch của nhân viên)
        confirmed = confirm_appointment(result.appointment_id, _time(p))
        if confirmed.ok:
            status, text = "CONFIRMED", "Đã đặt và xác nhận lịch."
        else:
            text = f"Đã đặt lịch nhưng chưa xác nhận được ({confirmed.message}). Hãy xác nhận ở trang Lịch hẹn."
    with SessionLocal() as db:
        label = SERVICE_LABEL.get(p["service_type"], "Lịch hẹn")
        at = f"{p['time']} " if p.get("time") else ""
        state = "Lịch đã được xác nhận." if status == "CONFIRMED" else "Lịch đang chờ nhân viên xác nhận."
        post_system(db, session_id, f"Lịch hẹn #{result.appointment_id} ({label}) đã được nhân viên đặt cho bạn vào "
                                    f"{at}ngày {_day(p):%d/%m/%Y}. {state}")
        db.commit()
    return True, f"Lịch #{result.appointment_id}: {text}"


def _reschedule(session_id: str, p: dict) -> tuple[bool, str]:
    if not (p.get("appointment_id") and p.get("date")):
        return False, "Thiếu mã lịch hoặc ngày mới"
    result = staff_reschedule(p["appointment_id"], _day(p), _time(p), p.get("reason"))
    return result.ok, result.message


def _cancel(session_id: str, p: dict) -> tuple[bool, str]:
    if not p.get("appointment_id"):
        return False, "Thiếu mã lịch"
    result = staff_cancel(p["appointment_id"], p.get("reason"))
    return result.ok, result.message


def _facts(session_id: str, values: dict) -> str:
    try:
        if update_known_facts(session_id, values):
            return ""
        return " (Trợ lý chưa có ngữ cảnh phiên này để cập nhật.)"
    except Exception:  # checkpoint lỗi không được làm mất cập nhật DB đã ghi; báo để nhân viên biết
        logger.exception("update_known_facts failed for %s", session_id)
        return " (Chưa cập nhật được ngữ cảnh của trợ lý, hãy nhắn lại cho trợ lý nếu cần.)"


def _append_summary(db, ticket_id: int, line: str) -> None:
    from app.db.models import HandoverTicket
    ticket = db.get(HandoverTicket, ticket_id)
    if ticket is not None:
        ticket.summary = f"{ticket.summary or ''}\n{line}".strip()


def _price(session_id: str, p: dict, ticket_id: int) -> tuple[bool, str]:
    if not p.get("price_vnd"):
        return False, "Thiếu giá chốt"
    values = {"decided_price": p["price_vnd"], **({"car_model": p["car_model"]} if p.get("car_model") else {})}
    with SessionLocal() as db:
        _append_summary(db, ticket_id, f"[Giá chốt] {fmt_vnd(p['price_vnd'])}" + (f" - {p['car_model']}" if p.get("car_model") else ""))
        db.commit()
    return True, f"Đã ghi giá chốt {fmt_vnd(p['price_vnd'])} cho trợ lý.{_facts(session_id, values)}"


def _customer(session_id: str, p: dict, ticket_id: int) -> tuple[bool, str]:
    if not any(p.get(k) for k in ("name", "phone", "address")):
        return False, "Chưa có thông tin nào để cập nhật"
    notes = []
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        if p.get("phone"):
            _save_phone(db, session, p["phone"])
        if p.get("name"):
            sync_contact(db, session, name=p["name"], name_corrected=True)
            customer = db.get(Customer, session.customer_id) if session.customer_id else None
            if customer is None or customer.full_name != p["name"]:
                notes.append("Tên chưa vào hồ sơ khách (cần họ tên đầy đủ hoặc SĐT đã xác minh), trợ lý vẫn dùng tên này"
                             if not is_full_name(p["name"]) else "Tên chưa vào hồ sơ khách vì SĐT đã xác minh")
        if p.get("address"):
            _append_summary(db, ticket_id, f"[Địa chỉ khách] {p['address']}")
        db.commit()
    values = {k: p[v] for k, v in (("customer_name", "name"), ("customer_phone", "phone"),
                                   ("customer_address", "address")) if p.get(v)}
    return True, "Đã cập nhật thông tin khách." + (f" {notes[0]}." if notes else "") + _facts(session_id, values)


def _note(session_id: str, p: dict, ticket_id: int) -> tuple[bool, str]:
    if not p.get("note"):
        return False, "Ghi chú đang trống"
    label = NOTE_TOPICS.get(p.get("topic"), NOTE_TOPICS["GENERAL"])
    with SessionLocal() as db:
        _append_summary(db, ticket_id, f"[Ghi chú NV · {label}] {p['note']}")
        db.commit()
    notes = _load_notes(session_id) + [f"[{label}] {p['note']}"]
    return True, "Đã lưu ghi chú." + _facts(session_id, {"staff_notes": notes[-NOTE_LIMIT:]})


def _load_notes(session_id: str) -> list[str]:
    from app.agent.graph import get_graph
    try:
        snap = get_graph().get_state({"configurable": {"thread_id": session_id}})
        fact = (snap.values or {}).get("known_facts", {}).get("staff_notes")
        value = getattr(fact, "value", None) if fact is not None and not isinstance(fact, dict) else (fact or {}).get("value")
        return list(value or [])
    except Exception:
        logger.exception("load staff_notes failed for %s", session_id)
        return []


def _execute(action: StaffAction, p: dict) -> tuple[bool, str]:
    t, sid = action.action_type, action.session_id
    if t == "BOOK_APPOINTMENT":
        return _book(sid, p)
    if t == "RESCHEDULE_APPOINTMENT":
        return _reschedule(sid, p)
    if t == "CANCEL_APPOINTMENT":
        return _cancel(sid, p)
    if t == "AGREED_PRICE":
        return _price(sid, p, action.ticket_id)
    if t == "CUSTOMER_INFO":
        return _customer(sid, p, action.ticket_id)
    return _note(sid, p, action.ticket_id)


@router.post("/staff-actions/{action_id}/apply")
def apply_action(action_id: int, body: ApplyBody | None = None, user: AdminUser = Depends(admin_user)):
    """Nhân viên duyệt một đề xuất (có thể kèm giá trị đã sửa). Thất bại (đầy chỗ, thiếu trường...) -> 409 và đề xuất
    vẫn chờ duyệt để sửa lại; thành công -> APPLIED, gọi lại trả 409."""
    with SessionLocal() as db:
        action = db.scalars(select(StaffAction).where(StaffAction.id == action_id).with_for_update()).one_or_none()
        if action is None:
            raise HTTPException(404, "Không tìm thấy đề xuất")
        _guard(db, user, action.ticket_id)
        if action.status != "PROPOSED":
            raise HTTPException(409, "Đề xuất này đã được xử lý")
        with _locks[action.session_id]:  # cùng khóa với lượt bot: không ghi checkpoint đè lên lượt đang chạy
            ctx = context_for(action.ticket_id)
            raw = body.params if body and body.params is not None else action.params
            params, warnings = normalize_params(action.action_type, raw, ctx["appointments"], ctx["customer"]["phone"])
            if warnings:
                raise HTTPException(422, "; ".join(warnings))
            ok, message = _execute(action, params)
            if not ok:
                raise HTTPException(409, message)
            action.status, action.params, action.summary = "APPLIED", params, describe(action.action_type, params)
            action.result, action.warnings = message, None
            action.decided_by, action.decided_at = user.id or None, datetime.now(timezone.utc)
            db.commit()
            return {"ok": True, "message": message, "action": serialize(action)}


@router.post("/staff-actions/{action_id}/dismiss")
def dismiss_action(action_id: int, user: AdminUser = Depends(admin_user)):
    """Bỏ một đề xuất (phân tích sai hoặc không cần). Không đổi gì ngoài trạng thái đề xuất."""
    with SessionLocal() as db:
        action = db.scalars(select(StaffAction).where(StaffAction.id == action_id).with_for_update()).one_or_none()
        if action is None:
            raise HTTPException(404, "Không tìm thấy đề xuất")
        _guard(db, user, action.ticket_id)
        if action.status != "PROPOSED":
            raise HTTPException(409, "Đề xuất này đã được xử lý")
        action.status, action.decided_by, action.decided_at = "DISMISSED", user.id or None, datetime.now(timezone.utc)
        db.commit()
        return {"ok": True}
