"""
[TRACK C] Conversation Manager: nhận tin -> khóa theo phiên -> kiểm tra mode -> chạy agent -> lưu DB.
- BOT: router chạy các agent chỉ đọc song song + tối đa một agent ghi dữ liệu.
- HUMAN_PENDING / HUMAN_ACTIVE: bot im lặng, lưu tin và bổ sung ngữ cảnh cho ticket nhân viên.
Thông báo chuyển giao (nhân viên tiếp nhận / trả về bot / quá hạn) là tin role="system", client poll để hiển thị.
Phiên = chat_sessions.id (UUID trình duyệt). TODO[PLATFORM]: _locks chỉ đúng khi chạy 1 process;
nhiều worker thì đổi sang pg_advisory_xact_lock hoặc SELECT ... FOR UPDATE trên chat_sessions.
"""
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent.runner import run_turn
from app.config import settings
from app.contracts.schemas import ChatRequest, ChatResponse, ConversationMode
from app.db.models import ChatSession, Message
from app.db.session import SessionLocal
from app.identity import attach_phone, get_or_create_session, verified_customer_id
from app.services.handover import open_ticket

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # mỗi phiên 1 khóa
TIMEOUT_NOTICE = ("Hiện nhân viên đang bận nên chưa tiếp nhận được. Bạn có thể gọi hotline {hotline} "
                  "để được hỗ trợ ngay. Các tin nhắn của bạn vẫn được lưu để nhân viên tiếp nhận.")
PHONE_RE = re.compile(r"(?<!\d)((?:\+84|0)\d{9})(?!\d)")  # search for phone number


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timeout_notice(db: Session, session: ChatSession) -> str | None:
    """HUMAN_PENDING quá sla_due_at mà chưa ai nhận -> báo hotline (1 lần cho mỗi ticket)."""
    ticket = open_ticket(db, session.id)
    # if the mode does not reuqire human intervention, no pending ticket exists
    # and the ticket is not open then ignore time_out
    if session.mode != ConversationMode.HUMAN_PENDING.value or not ticket or ticket.status != "OPEN":
        return None
    # the waiting time hasnt exceeced the limit yet
    if ticket.sla_due_at is None or _now() < ticket.sla_due_at:
        return None

    notice = TIMEOUT_NOTICE.format(hotline=settings.hotline)
    already = db.query(Message).filter(Message.session_id == session.id, Message.role == "system",
                                       Message.content == notice, Message.created_at >= ticket.created_at).first()
    return None if already else notice


def _save_phone(db: Session, session: ChatSession, phone: str) -> None:
    """Gắn SĐT vào phiên/khách và ghi vào ticket đang mở để nhân viên liên hệ."""
    attach_phone(db, session, phone)
    ticket = open_ticket(db, session.id)
    if ticket and phone not in (ticket.summary or ""):
        ticket.summary = f"{ticket.summary or ''}\nSĐT khách: {phone}".strip()


def handle_message(req: ChatRequest) -> ChatResponse:
    # Ghi nhận phiên và lưu tin của khách trước khi lấy khóa
    with SessionLocal() as db:
        session = get_or_create_session(db, req.session_id)
        user_msg = Message(session_id=session.id, role="user", content=req.message)
        db.add(user_msg)
        session.last_message_at = _now()
        db.commit()
        session_id = session.id

    with _locks[session_id]:
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            if session.mode in (ConversationMode.HUMAN_ACTIVE.value, ConversationMode.HUMAN_PENDING.value):
                # Không gọi graph/LLM; nhân viên xem tin đã lưu qua inbox hiện có.
                ticket = open_ticket(db, session_id)
                if ticket:
                    ticket.summary = f"{ticket.summary or ''}\n[Khách] {req.message}".strip()
                if m := PHONE_RE.search(req.message):
                    _save_phone(db, session, m.group(1))
                if notice := _timeout_notice(db, session):
                    db.add(Message(session_id=session_id, role="system", content=notice))
                db.commit()
                # Giữ cursor ở tin khách để client còn poll được thông báo timeout vừa tạo.
                return ChatResponse(session_id=session_id, reply="", mode=ConversationMode(session.mode),
                                    message_id=user_msg.id)
            negotiation_active, mode = session.negotiation_active, session.mode
            customer_id = verified_customer_id(db, session)

        result = run_turn(session_id, customer_id, req.message, negotiation_active, mode)

        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            session.negotiation_active = session.negotiation_active or bool(result.get("negotiation_active"))  # sticky
            # the response requires the system to change mode or not
            if result.get("set_mode") and session.mode == ConversationMode.BOT.value:  # không ghi đè khi nhân viên vừa nhận
                session.mode = result["set_mode"]

            # update the customer's phone number both in customers and in ticket summary
            # LLM preprocess stores phone as "customer_phone"; rule-based stores it as "phone"
            _slots = result.get("slots", {})
            if phone := (_slots.get("phone") or _slots.get("customer_phone")):
                _save_phone(db, session, phone)

            # record the bot reply to the database
            reply, trace = result["final_reply"], result["trace"]
            if notice := _timeout_notice(db, session):
                db.add(Message(session_id=session_id, role="system", content=notice))
                db.flush()  # tin system có id nhỏ hơn tin bot -> client poll sau message_id không bị lặp
                reply = f"{reply}\n\n{notice}"
            bot_msg = Message(session_id=session_id, role="ai", content=reply, trace=trace)
            db.add(bot_msg)
            session.last_message_at = _now()
            db.commit()
            return ChatResponse(session_id=session_id, reply=reply, mode=ConversationMode(session.mode),
                                trace=trace, message_id=bot_msg.id)


def post_system(db: Session, session_id: str, content: str, role: str = "system",
                manager_id: int | None = None) -> Message:
    """Ghi tin hệ thống/nhân viên vào phiên (dùng cho admin API)."""
    msg = Message(session_id=session_id, role=role, content=content, manager_id=manager_id)
    db.add(msg)
    return msg


def messages_since(session_id: str, after: int) -> dict | None:
    """Tin nhân viên/hệ thống mới hơn `after` cho client poll. None nếu phiên không tồn tại.
    Chỉ trình duyệt giữ UUID phiên mới biết session_id (UUID khó đoán), nên không cần token riêng."""
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        if session is None:
            return None
        rows = (db.query(Message).filter(Message.session_id == session_id, Message.id > after,
                                         Message.role.in_(["staff", "system"]))
                .order_by(Message.id).all())
        return {"mode": session.mode,
                "messages": [{"id": m.id, "role": m.role, "content": m.content} for m in rows]}
