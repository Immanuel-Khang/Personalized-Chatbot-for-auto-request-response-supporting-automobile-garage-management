"""
[TRACK C] Conversation Manager: nhận tin -> khóa theo thread -> kiểm tra mode -> chạy agent -> lưu DB.
- BOT / HUMAN_PENDING: bot vẫn trả lời (khách không bị "treo" trong lúc chờ nhân viên).
  Yêu cầu cần người phát sinh thêm khi đang chờ -> ghi vào ticket đang mở (human_handover).
- HUMAN_ACTIVE: nhân viên đã nhận -> bot im lặng, chỉ lưu tin; nhân viên trả lời qua admin API.
Thông báo chuyển giao (nhân viên tiếp nhận / trả về bot / quá hạn) là tin role="system", client poll để hiển thị.
"""
import json
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.agent.runner import run_turn
from app.config import settings
from app.contracts.schemas import ChatRequest, ChatResponse, ConversationMode
from app.db.models import Conversation, HumanTask, Message
from app.db.session import SessionLocal
from app.identity import resolve_customer

_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)  # mỗi hội thoại 1 khóa
TIMEOUT_NOTICE = ("Hiện nhân viên đang bận nên chưa tiếp nhận được. Bạn có thể gọi hotline {hotline} "
                  "để được hỗ trợ ngay, mình vẫn ở đây trả lời các câu hỏi khác của bạn.")
PHONE_RE = re.compile(r"\b(0\d{9})\b")  # search for phone number


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _open_task(db: Session, conv_id: int) -> HumanTask | None:
    return (db.query(HumanTask).filter(HumanTask.conversation_id == conv_id, HumanTask.status != "RESOLVED")
            .order_by(HumanTask.id.desc()).first())


def _timeout_notice(db: Session, conv: Conversation) -> str | None:
    """HUMAN_PENDING quá HANDOVER_TIMEOUT_MINUTES mà chưa ai nhận -> báo hotline (1 lần cho mỗi ticket)."""
    task = _open_task(db, conv.id)
    # if the mode does not reuqire human intervention, no pending task exists
    # and the task is not open then ignore time_out
    if conv.mode != ConversationMode.HUMAN_PENDING.value or not task or task.status != "OPEN":
        return None
    # the waiting time hasnt exceeced the limit yet
    if _now() - task.created_at < timedelta(minutes=settings.handover_timeout_minutes):
        return None
    
    notice = TIMEOUT_NOTICE.format(hotline=settings.hotline)
    already = db.query(Message).filter(Message.conversation_id == conv.id, Message.role == "system",
                                       Message.content == notice, Message.created_at >= task.created_at).first()
    return None if already else notice


def handle_message(req: ChatRequest) -> ChatResponse:
    # Resolve customer's identity and commit their message to db
    # Important: extract or create the conv_id to retreive the lock
    with SessionLocal() as db:
        customer = resolve_customer(db, req.visitor_token)
        conv = db.get(Conversation, req.conversation_id) if req.conversation_id else None
        if conv is None or conv.customer_id != customer.id:
            conv = Conversation(customer_id=customer.id)
            db.add(conv)
            db.flush()
        user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
        db.add(user_msg)
        db.commit()
        conv_id, customer_id = conv.id, customer.id

    with _locks[conv_id]:
        with SessionLocal() as db:
            conv = db.get(Conversation, conv_id)
            if conv.mode == ConversationMode.HUMAN_ACTIVE.value:
                # Nhân viên đang trò chuyện trực tiếp: bot không chen vào, chỉ lưu SĐT nếu khách gửi
                if m := PHONE_RE.search(req.message):
                    resolve_customer(db, req.visitor_token).phone = m.group(1)
                db.commit()
                return ChatResponse(conversation_id=conv_id, reply="", 
                                    mode=ConversationMode.HUMAN_ACTIVE,
                                    message_id=user_msg.id
                                    )
            negotiation_active, mode = conv.negotiation_active, conv.mode

        result = run_turn(conv_id, customer_id, req.message, negotiation_active, mode)

        with SessionLocal() as db:
            conv = db.get(Conversation, conv_id) 
            conv.negotiation_active = conv.negotiation_active or bool(result.get("negotiation_active"))   # sticky
            # the response requires the system to change mode or not
            if result.get("set_mode") and conv.mode == ConversationMode.BOT.value:  # không ghi đè khi nhân viên vừa nhận
                conv.mode = result["set_mode"]
            
            # update the customer's phone number both in Customer database and in task summary
            # LLM preprocess stores phone as "customer_phone"; rule-based stores it as "phone"
            _slots = result.get("slots", {})
            if phone := (_slots.get("phone") or _slots.get("customer_phone")):
                resolve_customer(db, req.visitor_token).phone = phone
                task = _open_task(db, conv_id)
                if task and phone not in task.summary:
                    task.summary = f"{task.summary}\nSĐT khách: {phone}".strip()
            
            # record the bot reply to the database
            reply, trace = result["final_reply"], result["trace"]
            if notice := _timeout_notice(db, conv):
                db.add(Message(conversation_id=conv_id, role="system", content=notice))
                db.flush()  # tin system có id nhỏ hơn tin bot -> client poll sau message_id không bị lặp
                reply = f"{reply}\n\n{notice}"
            bot_msg = Message(conversation_id=conv_id, role="bot", content=reply,
                              trace=json.dumps(trace, ensure_ascii=False))
            db.add(bot_msg)
            db.commit()
            return ChatResponse(conversation_id=conv_id, reply=reply, mode=ConversationMode(conv.mode),
                                trace=trace, message_id=bot_msg.id)


def post_system(db: Session, conv_id: int, content: str, role: str = "system") -> Message:
    """Ghi tin hệ thống/nhân viên vào hội thoại (dùng cho admin API)."""
    msg = Message(conversation_id=conv_id, role=role, content=content)
    db.add(msg)
    return msg


def messages_since(conv_id: int, visitor_token: str, after: int) -> dict | None:
    """Tin nhân viên/hệ thống mới hơn `after` cho client poll. None nếu hội thoại không thuộc khách này."""
    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        customer = resolve_customer(db, visitor_token)
        if conv is None or conv.customer_id != customer.id:
            return None
        rows = (db.query(Message).filter(Message.conversation_id == conv_id, Message.id > after,
                                         Message.role.in_(["staff", "system"]))
                .order_by(Message.id).all())
        return {"mode": conv.mode,
                "messages": [{"id": m.id, "role": m.role, "content": m.content} for m in rows]}
