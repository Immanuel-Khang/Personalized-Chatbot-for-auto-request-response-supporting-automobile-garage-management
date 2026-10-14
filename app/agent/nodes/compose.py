"""[TRACK B] Compose: ghép phần trả lời của các agent theo thứ tự task, trước Output Guard.
Ghép cố định bằng code (không để LLM viết lại) để số tiền giữ nguyên như tool trả về.
Một agent cần người (vd không tìm thấy tài liệu) không làm mất phần trả lời của các agent khác."""
from app.agent.nodes.common import step
from app.agent.state import AgentState


def compose(state: AgentState) -> dict:
    answers = sorted(state.get("answers", []), key=lambda a: a.get("order", 0))
    texts = [a["text"].strip() for a in answers if not a.get("needs_human") and a.get("text", "").strip()]
    reasons = [a.get("handover_reason") or a["agent"].upper() for a in answers if a.get("needs_human")]
    update: dict = {"draft_reply": "\n\n".join(texts), "trace": step(state, "compose")}
    if reasons:
        update.update(needs_human=True, handover_reason="; ".join(dict.fromkeys(reasons)))
    return update
