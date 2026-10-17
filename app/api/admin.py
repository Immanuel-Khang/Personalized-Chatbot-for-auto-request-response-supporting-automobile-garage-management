"""[TRACK C] Admin API tối thiểu. TODO[PLATFORM]: đăng nhập managers (password_hash) + RBAC; hiện CHƯA có xác thực,
không được mở ra internet. Luồng chuyển giao: OPEN (bot im lặng) -> claim (HUMAN_ACTIVE, báo khách) -> reply
-> resolve (trả về bot, báo khách)."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.contracts.schemas import ConversationMode
from app.conversation import _locks, post_system
from app.db.models import ChatSession, HandoverTicket, Manager, Message
from app.db.session import SessionLocal

router = APIRouter(prefix="/admin")

CLAIMED_NOTICE = "Nhân viên {assignee} đã tiếp nhận cuộc trò chuyện và sẽ hỗ trợ bạn trực tiếp từ bây giờ."
RESOLVED_NOTICE = "Nhân viên đã hoàn tất hỗ trợ. Trợ lý tự động sẽ tiếp tục đồng hành cùng bạn, cần gì bạn cứ nhắn nhé."


class StaffReply(BaseModel):
    content: str


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


@router.get("/tasks")
def list_tasks(status: str = "OPEN"):
    with SessionLocal() as db:
        rows = db.query(HandoverTicket).filter_by(status=status).order_by(HandoverTicket.created_at).all()
        return [{"id": t.id, "session_id": t.session_id, "reason": t.reason, "summary": t.summary,
                 "assigned_manager_id": t.assigned_manager_id, "sla_due_at": t.sla_due_at} for t in rows]


@router.get("/tasks/{ticket_id}/messages")
def task_messages(ticket_id: int):
    """Toàn bộ lịch sử phiên để nhân viên đọc ngữ cảnh trước/sau khi nhận."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        rows = db.query(Message).filter_by(session_id=ticket.session_id).order_by(Message.id).all()
        return [{"id": m.id, "role": m.role, "content": m.content} for m in rows]


@router.post("/tasks/{ticket_id}/claim")
def claim_task(ticket_id: int, manager: str | None = None):
    """Nhân viên (username trong managers) nhận ticket: HUMAN_PENDING -> HUMAN_ACTIVE, bot ngừng trả lời, khách được báo."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        staff = _get_manager(db, manager)
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
def staff_reply(ticket_id: int, body: StaffReply):
    """Nhân viên nhắn cho khách (chỉ khi đã nhận ticket)."""
    with SessionLocal() as db:
        ticket = _get_ticket(db, ticket_id)
        if ticket.status != "CLAIMED":
            raise HTTPException(409, "claim the ticket before replying")
        msg = post_system(db, ticket.session_id, body.content, role="staff", manager_id=ticket.assigned_manager_id)
        db.commit()
        return {"ok": True, "message_id": msg.id}


@router.post("/tasks/{ticket_id}/resolve")
def resolve_task(ticket_id: int):
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
