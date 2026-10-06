"""[TRACK C] Admin API tối thiểu. TODO[PLATFORM]: RBAC/xác thực nhân viên, upload knowledge.
Luồng chuyển giao: OPEN (bot vẫn trả lời) -> claim (HUMAN_ACTIVE, báo khách) -> reply -> resolve (trả về bot, báo khách)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.contracts.schemas import ConversationMode
from app.conversation import _locks, post_system
from app.db.models import Conversation, HumanTask, Message
from app.db.session import SessionLocal

router = APIRouter(prefix="/admin")

CLAIMED_NOTICE = "Nhân viên {assignee} đã tiếp nhận cuộc trò chuyện và sẽ hỗ trợ bạn trực tiếp từ bây giờ."
RESOLVED_NOTICE = "Nhân viên đã hoàn tất hỗ trợ. Trợ lý tự động sẽ tiếp tục đồng hành cùng bạn, cần gì bạn cứ nhắn nhé."


class StaffReply(BaseModel):
    content: str


def _get_task(db, task_id: int) -> HumanTask:
    task = db.get(HumanTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@router.get("/tasks")
def list_tasks(status: str = "OPEN"):
    with SessionLocal() as db:
        rows = db.query(HumanTask).filter_by(status=status).all()
        return [{"id": t.id, "conversation_id": t.conversation_id, "reason": t.reason, "summary": t.summary,
                 "assignee": t.assignee} for t in rows]


@router.get("/tasks/{task_id}/messages")
def task_messages(task_id: int):
    """Toàn bộ lịch sử hội thoại để nhân viên đọc ngữ cảnh trước/sau khi nhận."""
    with SessionLocal() as db:
        task = _get_task(db, task_id)
        rows = db.query(Message).filter_by(conversation_id=task.conversation_id).order_by(Message.id).all()
        return [{"id": m.id, "role": m.role, "content": m.content} for m in rows]


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: int, assignee: str = "tư vấn"):
    """Nhân viên nhận ticket: HUMAN_PENDING -> HUMAN_ACTIVE, bot ngừng trả lời, khách được báo."""
    with SessionLocal() as db:
        task = _get_task(db, task_id)
        if task.status != "OPEN":
            raise HTTPException(409, f"task is {task.status}")
        with _locks[task.conversation_id]:  # không chen giữa lúc bot đang trả lời lượt hiện tại
            task.status, task.assignee = "CLAIMED", assignee
            db.get(Conversation, task.conversation_id).mode = ConversationMode.HUMAN_ACTIVE.value
            post_system(db, task.conversation_id, CLAIMED_NOTICE.format(assignee=assignee))
            db.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/reply")
def staff_reply(task_id: int, body: StaffReply):
    """Nhân viên nhắn cho khách (chỉ khi đã nhận ticket)."""
    with SessionLocal() as db:
        task = _get_task(db, task_id)
        if task.status != "CLAIMED":
            raise HTTPException(409, "claim the task before replying")
        msg = post_system(db, task.conversation_id, body.content, role="staff")
        db.commit()
        return {"ok": True, "message_id": msg.id}


@router.post("/tasks/{task_id}/resolve")
def resolve_task(task_id: int):
    """Đóng ticket, trả hội thoại về cho bot và báo khách."""
    with SessionLocal() as db:
        task = _get_task(db, task_id)
        with _locks[task.conversation_id]:
            was_claimed = task.status == "CLAIMED"
            task.status = "RESOLVED"
            db.get(Conversation, task.conversation_id).mode = ConversationMode.BOT.value
            if was_claimed:  # ticket chưa ai nhận thì khách chưa từng nói chuyện với người -> không cần báo
                post_system(db, task.conversation_id, RESOLVED_NOTICE)
            db.commit()
    return {"ok": True}
