"""[TRACK C] HandoverService: tạo ticket + lead cho nhân viên. TODO: SLA, phân công, thông báo."""
from app.contracts.schemas import HandoverResult
from app.db.models import Conversation, HumanTask, Lead
from app.db.session import SessionLocal


class DbHandoverService:
    def create(self, conversation_id: int, reason: str, summary: str) -> HandoverResult:
        with SessionLocal() as db:
            conv = db.get(Conversation, conversation_id)
            task = HumanTask(conversation_id=conversation_id, reason=reason, summary=summary)
            lead = Lead(customer_id=conv.customer_id, conversation_id=conversation_id, note=summary)
            db.add_all([task, lead])
            db.commit()
            return HandoverResult(task_id=task.id, lead_id=lead.id)

    def append(self, conversation_id: int, reason: str, note: str) -> HandoverResult:
        """Đang chờ nhân viên mà khách nêu thêm yêu cầu cần người -> ghi vào ticket đang mở, không tạo ticket mới."""
        with SessionLocal() as db:
            task = (db.query(HumanTask)
                    .filter(HumanTask.conversation_id == conversation_id, HumanTask.status != "RESOLVED")
                    .order_by(HumanTask.id.desc()).first())
            if task is None:
                return self.create(conversation_id, reason, note)
            task.summary = f"{task.summary}\n[{reason}] {note}".strip()
            db.commit()
            return HandoverResult(task_id=task.id)
