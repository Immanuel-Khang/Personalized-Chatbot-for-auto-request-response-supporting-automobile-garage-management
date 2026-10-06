"""
[TRACK B] Policy Guard: NƠI DUY NHẤT quyết định chính sách (mặc cả/giảm giá, đòi gặp người, khiếu nại, an toàn).
Bot KHÔNG có quyền duyệt giảm giá: mọi yêu cầu mặc cả/giảm giá đều chuyển người.
negotiation_active là cờ sticky (đọc/ghi bảng conversations): đã bật thì giữ.
"""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent

HUMAN_REQUEST = ["gặp nhân viên", "nói chuyện với người", "gặp người thật", "tư vấn viên"]
COMPLAINT = ["khiếu nại", "phàn nàn", "tệ quá", "không hài lòng", "lừa đảo"]
SAFETY = ["mất phanh", "phanh không ăn", "cháy", "khói", "đèn dầu sáng", "đèn áp suất dầu"]


def policy_guard(state: AgentState) -> dict:
    text = state["user_text"].lower()
    intents = state.get("intents", [])
    negotiation = state.get("negotiation_active", False)
    safety = any(k in text for k in SAFETY)

    reason, route = None, "router"
    if Intent.DISCOUNT.value in intents:
        negotiation = True
        reason, route = "NEGOTIATION", "human"
    elif any(k in text for k in HUMAN_REQUEST):
        reason, route = "HUMAN_REQUEST", "human"
    elif any(k in text for k in COMPLAINT):
        reason, route = "COMPLAINT", "human"
    elif safety:
        route = "knowledge"

    return {
        "negotiation_active": negotiation,
        "safety": safety,
        "route": route,
        "handover_reason": reason,
        "trace": step(state, "policy_guard"),
    }
