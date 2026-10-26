"""Apply explicit safety and handover policy before task routing."""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent

HUMAN_REQUEST = ["gặp nhân viên", "nói chuyện với người", "gặp người thật", "tư vấn viên", "cho tôi gặp", "cho mình gặp", "kết nối với người"]
COMPLAINT = ["khiếu nại", "phàn nàn", "tệ quá", "không hài lòng", "lừa đảo", "bực mình", "thất vọng", "kém quá"]
SAFETY = ["mất phanh", "phanh không ăn", "cháy", "khói", "đèn dầu sáng", "đèn áp suất dầu", "bốc mùi khét", "xe không lái được", "tay lái nặng", "mất lái"]


def policy_guard(state: AgentState) -> dict:
    text = state.get("user_text", "").casefold()
    interpretation = state.get("conversation_interpretation")
    tasks = interpretation.tasks if interpretation else []
    negotiation = state.get("negotiation_active", False)
    is_safety = any(marker in text for marker in SAFETY)
    reasons: list[str] = []
    if any(marker in text for marker in HUMAN_REQUEST):
        reasons.append("HUMAN_REQUEST")
    if any(marker in text for marker in COMPLAINT):
        reasons.append("COMPLAINT")
    if any(task.domain == Intent.DISCOUNT for task in tasks):
        negotiation = True
        reasons.append("NEGOTIATION")
    if interpretation and interpretation.complexity == "HIGH" and any(
        task.domain in (Intent.KNOWLEDGE, Intent.MAINTENANCE) for task in tasks
    ):
        reasons.append("COMPLEXITY_HIGH")
    return {
        "negotiation_active": negotiation,
        "safety": is_safety,
        "route": "human" if reasons else "router",
        "handover_reason": "; ".join(reasons) or None,
        "trace": step(state, "policy_guard"),
    }
