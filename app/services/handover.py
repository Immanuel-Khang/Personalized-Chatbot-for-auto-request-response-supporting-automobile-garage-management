"""[TRACK C] HandoverService: tạo ticket cho nhân viên. TODO: phân công, thông báo.
reason trong ticket là một mã chính (CHECK theo schema); chuỗi lý do đầy đủ từ agent ghi vào summary."""
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.contracts.schemas import HandoverResult
from app.db.models import HANDOVER_REASONS, ChatSession, HandoverTicket
from app.db.session import SessionLocal

# Mã agent dùng mà schema không có -> mã tương ứng trong schema
_ALIASES = {"CONTRACT_OR_PRICE_CHANGE": "DEAL_CHANGE"}


def primary_reason(reason: str) -> str:
    """'HUMAN_REQUEST; NEGOTIATION' -> 'HUMAN_REQUEST'; 'COMPLEXITY_HIGH: no RAG match' -> 'COMPLEXITY_HIGH'."""
    for part in reason.split(";"):
        code = part.split(":")[0].strip().upper()
        code = _ALIASES.get(code, code)
        if code in HANDOVER_REASONS:
            return code
    return "OTHER"


def open_ticket(db, session_id: str) -> HandoverTicket | None:
    return (db.query(HandoverTicket)
            .filter(HandoverTicket.session_id == session_id, HandoverTicket.status != "RESOLVED")
            .order_by(HandoverTicket.id.desc()).first())


class DbHandoverService:
    def create(self, session_id: str, reason: str, summary: str) -> HandoverResult:
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            now = datetime.now(timezone.utc)
            ticket = HandoverTicket(session_id=session_id, customer_id=session.customer_id,
                                    reason=primary_reason(reason), summary=f"[{reason}] {summary}".strip(),
                                    sla_due_at=now + timedelta(minutes=settings.handover_timeout_minutes))
            db.add(ticket)
            db.commit()
            return HandoverResult(task_id=ticket.id)

    def append(self, session_id: str, reason: str, note: str) -> HandoverResult:
        """Đang chờ nhân viên mà khách nêu thêm yêu cầu cần người -> ghi vào ticket đang mở, không tạo ticket mới."""
        with SessionLocal() as db:
            ticket = open_ticket(db, session_id)
            if ticket is None:
                return self.create(session_id, reason, note)
            ticket.summary = f"{ticket.summary or ''}\n[{reason}] {note}".strip()
            db.commit()
            return HandoverResult(task_id=ticket.id)
