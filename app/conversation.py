"""
[TRACK C] Conversation Manager: nhận tin -> khóa theo thread -> kiểm tra mode -> chạy agent -> lưu DB.
mode != BOT thì KHÔNG gọi agent (nhân viên đang xử lý).
"""
import json
import threading
from collections import defaultdict

from app.agent.runner import run_turn
from app.contracts.schemas import ChatRequest, ChatResponse, ConversationMode
from app.db.models import Conversation, Message
from app.db.session import SessionLocal
from app.identity import resolve_customer

_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)  # mỗi hội thoại 1 khóa
WAIT_REPLY = "Nhân viên đang xử lý yêu cầu của bạn, vui lòng chờ trong giây lát."


def handle_message(req: ChatRequest) -> ChatResponse:
    with SessionLocal() as db:
        customer = resolve_customer(db, req.visitor_token)
        conv = db.get(Conversation, req.conversation_id) if req.conversation_id else None
        if conv is None:
            conv = Conversation(customer_id=customer.id)
            db.add(conv)
            db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=req.message))
        db.commit()
        conv_id, customer_id = conv.id, customer.id

    with _locks[conv_id]:
        with SessionLocal() as db:
            conv = db.get(Conversation, conv_id)
            if conv.mode != ConversationMode.BOT.value:
                db.add(Message(conversation_id=conv_id, role="bot", content=WAIT_REPLY))
                db.commit()
                return ChatResponse(conversation_id=conv_id, reply=WAIT_REPLY, mode=ConversationMode(conv.mode))

        result = run_turn(conv_id, customer_id, req.message)

        with SessionLocal() as db:
            conv = db.get(Conversation, conv_id)
            conv.negotiation_active = bool(result.get("negotiation_active"))
            if result.get("set_mode"):
                conv.mode = result["set_mode"]
            if phone := result.get("slots", {}).get("phone"):
                resolve_customer(db, req.visitor_token).phone = phone
            reply, trace = result["final_reply"], result["trace"]
            db.add(Message(conversation_id=conv_id, role="bot", content=reply,
                           trace=json.dumps(trace, ensure_ascii=False)))
            db.commit()
            return ChatResponse(conversation_id=conv_id, reply=reply, mode=ConversationMode(conv.mode), trace=trace)
