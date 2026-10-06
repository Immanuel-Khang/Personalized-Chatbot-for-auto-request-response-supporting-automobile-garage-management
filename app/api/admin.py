"""[TRACK C] Admin API tối thiểu. TODO[PLATFORM]: RBAC, claim task, nhân viên trả lời khách, upload knowledge."""
from fastapi import APIRouter, HTTPException

from app.db.models import Conversation, HumanTask
from app.db.session import SessionLocal

router = APIRouter(prefix="/admin")


@router.get("/tasks")
def list_tasks(status: str = "OPEN"):
    with SessionLocal() as db:
        rows = db.query(HumanTask).filter_by(status=status).all()
        return [{"id": t.id, "conversation_id": t.conversation_id, "reason": t.reason, "summary": t.summary} for t in rows]


@router.post("/tasks/{task_id}/resolve")
def resolve_task(task_id: int):
    """Đóng ticket và trả hội thoại về cho bot."""
    with SessionLocal() as db:
        task = db.get(HumanTask, task_id)
        if not task:
            raise HTTPException(404, "task not found")
        task.status = "RESOLVED"
        db.get(Conversation, task.conversation_id).mode = "BOT"
        db.commit()
    return {"ok": True}
