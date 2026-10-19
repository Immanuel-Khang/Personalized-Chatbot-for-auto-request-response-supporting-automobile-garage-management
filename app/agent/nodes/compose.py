"""[TRACK B] Compose: ghép phần trả lời của các agent theo thứ tự task, trước Output Guard.
Ghép cố định bằng code (không để LLM viết lại) để số tiền giữ nguyên như tool trả về.
Một agent cần người (vd không tìm thấy tài liệu) không làm mất phần trả lời của các agent khác."""
from app.agent.nodes.common import step, strip_greeting
from app.agent.state import AgentState
from app.contracts.schemas import Intent


def compose(state: AgentState) -> dict:
    answers = sorted(state.get("answers", []), key=lambda a: a.get("order", 0))
    texts = [a["text"].strip() for a in answers if not a.get("needs_human") and a.get("text", "").strip()]
    # Chỉ chào khi khách chào; còn lại bỏ "Chào Khang," ở đầu mỗi phần trả lời (LLM hay tự chào lại mỗi lượt)
    if Intent.GREETING.value not in state.get("intents", []):
        texts = [strip_greeting(t) for t in texts]
    reasons = [a.get("handover_reason") or a["agent"].upper() for a in answers if a.get("needs_human")]
    update: dict = {"draft_reply": "\n\n".join(texts), "trace": step(state, "compose")}
    if reasons:
        update.update(needs_human=True, handover_reason="; ".join(dict.fromkeys(reasons)))
    return update
