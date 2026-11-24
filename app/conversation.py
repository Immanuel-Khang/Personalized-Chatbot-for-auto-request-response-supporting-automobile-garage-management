"""
[TRACK C] Conversation Manager: nhận tin -> khóa theo phiên -> kiểm tra mode -> chạy agent -> lưu DB.
- BOT: router chạy các agent chỉ đọc song song + tối đa một agent ghi dữ liệu.
- HUMAN_PENDING (chờ nhân viên nhận): bot vẫn trả lời; yêu cầu cần người nữa được ghi thêm vào ticket đang mở.
- HUMAN_ACTIVE (nhân viên đã nhận): bot im lặng, lưu tin và bổ sung ngữ cảnh cho ticket nhân viên.
Thông báo chuyển giao (nhân viên tiếp nhận / trả về bot / quá hạn) là tin role="system", client poll để hiển thị.
Phiên = chat_sessions.id (UUID trình duyệt). TODO[PLATFORM]: _locks chỉ đúng khi chạy 1 process;
nhiều worker thì đổi sang pg_advisory_xact_lock hoặc SELECT ... FOR UPDATE trên chat_sessions.
"""
import re
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent.runner import memory_checkpoint, run_turn
from app.config import settings
from app.contracts.schemas import ChatRequest, ChatResponse, ConversationMode, FactSource, KnownFact
from app.db.models import Appointment, ChatSession, Message
from app.db.session import SessionLocal
from app.identity import (customer_profile, get_or_create_session, normalize_phone, sync_contact,
                          verified_customer_id)
from app.services.handover import open_ticket

_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # mỗi phiên 1 khóa
logger = logging.getLogger(__name__)
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
    sync_contact(db, session, phone)
    if contact := normalize_phone(phone):
        # A correction after booking must reach the request staff will call back; decided rows stay untouched.
        db.query(Appointment).filter(Appointment.session_id == session.id,
                                     Appointment.status == "PENDING_APPROVAL",
                                     Appointment.contact_phone != contact).update(
            {Appointment.contact_phone: contact}, synchronize_session=False)
    ticket = open_ticket(db, session.id)
    if ticket and phone not in (ticket.summary or ""):
        ticket.summary = f"{ticket.summary or ''}\nSĐT khách: {phone}".strip()


def _fact(value) -> KnownFact | None:
    """Checkpoint state may hold a KnownFact or its dict form."""
    if value is None or isinstance(value, KnownFact):
        return value
    return KnownFact.model_validate(value) if isinstance(value, dict) else None


def _history_cursor(db: Session, session_id: str, current_id: int, summarized: int) -> int | None:
    """Upgrade a legacy count-based summary cursor to the exact message ID once."""
    if summarized <= 0:
        return None
    previous = (Message.role == "ai") | ((Message.role == "user") & (Message.id < current_id))
    return (db.query(Message.id).filter(Message.session_id == session_id, previous)
            .order_by(Message.id).offset(summarized - 1).limit(1).scalar())


def _history(db: Session, session_id: str, current_id: int, after_id: int | None = None) -> list[dict]:
    """Tin user/ai của phiên trước tin hiện tại, theo id (thứ tự ghi DB, xác định). Gọi trong khóa phiên.
    Mọi tin ai đều thuộc lượt đã xong (lượt này chưa ghi trả lời), kể cả tin có id lớn hơn tin hiện tại khi khách
    gửi dồn; tin user id >= current_id là tin hiện tại hoặc tin đang chờ khóa -> bỏ. Tin staff/system không vào LLM."""
    rows = (db.query(Message.id, Message.role, Message.content)
            .filter(Message.session_id == session_id,
                    *((Message.id > after_id,) if after_id is not None else ()),
                    (Message.role == "ai") | ((Message.role == "user") & (Message.id < current_id)))
            .order_by(Message.id).all())
    return [{"id": message_id, "role": role, "content": content} for message_id, role, content in rows]


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
            if session is None:
                raise RuntimeError(f"Chat session {session_id!r} disappeared before processing")
            if session.mode == ConversationMode.HUMAN_ACTIVE.value:
                # Nhân viên đã nhận: bot không gọi graph/LLM; nhân viên xem tin đã lưu qua inbox hiện có.
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
            profile = customer_profile(db, session)
            customer_id = verified_customer_id(db, session)
            checkpoint_started = time.perf_counter()
            memory = memory_checkpoint(session_id)
            checkpoint_ms = (time.perf_counter() - checkpoint_started) * 1000
            summary = memory.get("conversation_summary")
            cutoff_id = memory.get("summarized_through_message_id")
            legacy_summarized = int(memory.get("summarized_messages") or 0)
            if cutoff_id is None and legacy_summarized:
                cutoff_id = _history_cursor(db, session_id, user_msg.id, legacy_summarized)
                if cutoff_id is None:
                    # The old count no longer fits this session's rows; discard its stale summary.
                    summary, legacy_summarized = None, 0
            history_started = time.perf_counter()
            history = _history(db, session_id, user_msg.id, cutoff_id)
            logger.info("conversation memory load: checkpoint_ms=%.1f history_query_ms=%.1f loaded=%d after_message_id=%s",
                        checkpoint_ms, (time.perf_counter() - history_started) * 1000,
                        len(history), cutoff_id)
            previous_turn = memory.get("turn")
            turn = int(previous_turn) + 2 if previous_turn is not None else legacy_summarized + len(history)

        result = run_turn(session_id, customer_id, req.message, negotiation_active, mode, history, profile,
                          summary, cutoff_id, turn)

        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            if session is None:
                raise RuntimeError(f"Chat session {session_id!r} disappeared during processing")
            session.negotiation_active = session.negotiation_active or bool(result.get("negotiation_active"))  # sticky
            # the response requires the system to change mode or not
            if result.get("set_mode") and session.mode == ConversationMode.BOT.value:  # không ghi đè khi nhân viên vừa nhận
                session.mode = result["set_mode"]

            # Sync customers with the canonical fact store; latest phone/name (incl. corrections) wins.
            facts = result.get("known_facts", {}) or {}
            phone_fact = _fact(facts.get("customer_phone") or facts.get("phone"))
            name_fact = _fact(facts.get("customer_name"))
            phone = str(phone_fact.value) if phone_fact and phone_fact.value else None
            name = str(name_fact.value) if name_fact and name_fact.value else None
            if phone:
                _save_phone(db, session, phone)
            if name:
                sync_contact(db, session, name=name,
                             name_corrected=name_fact.source == FactSource.USER_CORRECTED)

            # record the bot reply to the database
            reply, trace = result["final_reply"], result["trace"]
            if session.mode == ConversationMode.HUMAN_PENDING.value and "human_handover" not in trace:
                # Chờ nhân viên nhưng bot vẫn trả lời được: lưu tin khách vào ticket làm ngữ cảnh.
                # (Lượt cần người đã được human_handover ghi vào ticket.)
                if ticket := open_ticket(db, session_id):
                    ticket.summary = f"{ticket.summary or ''}\n[Khách] {req.message}".strip()
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
