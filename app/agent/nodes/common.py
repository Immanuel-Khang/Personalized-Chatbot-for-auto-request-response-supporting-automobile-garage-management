import re

from app.agent.state import AgentState
from app.db.models import Message
from app.db.session import SessionLocal

# Quy tắc văn phong chung cho mọi prompt sinh câu trả lời
STYLE_RULES = """\
- KHÔNG mở đầu bằng lời chào hay gọi tên khách (VD "Chào Khang,") trừ khi khách vừa chào; đi thẳng vào nội dung.
- Nếu không chắc khách muốn gì, hỏi lại ngắn gọn để làm rõ; KHÔNG tự suy đoán.
"""

# "Chào Khang," / "Xin chào bạn!" / "Chào anh/chị." ở đầu câu trả lời
GREETING_PREFIX = re.compile(r"^\s*(?:xin\s+)?chào(?:\s+[^\s,.!\n]+){0,3}\s*[,.!]+\s*", re.IGNORECASE)


def step(state: AgentState, name: str) -> list[str]:
    """Node vừa đi qua; trace có reducer cộng dồn nên các nhánh song song không ghi đè nhau."""
    return [name]


def answer(state: AgentState, agent: str, text: str = "", needs_human: bool = False,
           handover_reason: str | None = None) -> dict:
    """Phần trả lời của một specialist; compose ghép theo task_order rồi mới qua Output Guard."""
    return {"agent": agent, "order": state.get("task_order", 0), "text": text,
            "needs_human": needs_human, "handover_reason": handover_reason}


def strip_greeting(text: str) -> str:
    """Bỏ lời chào ở đầu câu trả lời (dùng khi hội thoại đã bắt đầu)."""
    stripped = GREETING_PREFIX.sub("", text, count=1)
    if not stripped.strip() or stripped == text:
        return text
    return stripped[0].upper() + stripped[1:]


def conversation_history(session_id: str, limit: int = 8, max_chars: int = 300) -> str:
    """Lịch sử hội thoại gần nhất (cũ → mới) cho prompt."""
    with SessionLocal() as db:
        messages = (
            db.query(Message)
            .filter(Message.session_id == session_id, Message.role.in_(("user", "ai")))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
            .all()
        )
    if not messages:
        return "(chưa có tin nhắn trước đó)"
    return "\n".join(f"{'Khách' if m.role == 'user' else 'Bot'}: {m.content[:max_chars]}" for m in reversed(messages))
