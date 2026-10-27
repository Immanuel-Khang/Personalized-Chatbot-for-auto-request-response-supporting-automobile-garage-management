from app.agent.nodes.common import step, strip_greeting
from app.agent.state import AgentState
from app.contracts.schemas import Intent


def compose(state: AgentState) -> dict:
    answers = sorted(state.get("answers", []), key=lambda a: a.get("order", 0))
    texts = [a["text"].strip() for a in answers if not a.get("needs_human") and a.get("text", "").strip()]
    interpretation = state.get("conversation_interpretation")
    greeted = bool(interpretation and any(task.domain == Intent.GREETING for task in interpretation.tasks))
    if not greeted:
        texts = [strip_greeting(t) for t in texts]
    reasons = [a.get("handover_reason") or a["agent"].upper() for a in answers if a.get("needs_human")]
    update: dict = {"draft_reply": "\n\n".join(texts), "trace": step(state, "compose")}
    if reasons:
        update.update(needs_human=True, handover_reason="; ".join(dict.fromkeys(reasons)))
    return update
