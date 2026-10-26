"""
[TRACK B] Policy Guard: NƠI DUY NHẤT quyết định chính sách (mặc cả/giảm giá, đòi gặp người, khiếu nại, an toàn).
Bot KHÔNG có quyền duyệt giảm giá: mọi yêu cầu mặc cả/giảm giá đều chuyển người.
negotiation_active là cờ sticky (đọc/ghi bảng conversations): đã bật thì giữ.

Thứ tự ưu tiên:
  1. Safety → gắn cờ cảnh báo; router vẫn là nơi chọn agent
  2. Yêu cầu gặp người / khiếu nại / giảm giá → human
  3. Câu hỏi kỹ thuật phức tạp → human
  4. Mặc định → tiếp tục router
"""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent

# Khách muốn nói chuyện với người thật
HUMAN_REQUEST = [
    "gặp nhân viên", "nói chuyện với người", "gặp người thật",
    "tư vấn viên", "cho tôi gặp", "cho mình gặp", "kết nối với người",
]

# Khách không hài lòng / khiếu nại
COMPLAINT = [
    "khiếu nại", "phàn nàn", "tệ quá", "không hài lòng",
    "lừa đảo", "bực mình", "thất vọng", "kém quá",
]

# Dấu hiệu nguy hiểm cần cảnh báo an toàn
SAFETY = [
    "mất phanh", "phanh không ăn", "cháy", "khói",
    "đèn dầu sáng", "đèn áp suất dầu", "bốc mùi khét",
    "xe không lái được", "tay lái nặng", "mất lái",
]


def policy_guard(state: AgentState) -> dict:
    text = state.get("user_text", "").lower()
    intents = state.get("intents", [])
    negotiation = state.get("negotiation_active", False)

    is_safety = any(k in text for k in SAFETY)
    is_human_request = any(k in text for k in HUMAN_REQUEST)
    is_complaint = any(k in text for k in COMPLAINT)
    is_discount = Intent.DISCOUNT.value in intents

    # Ghi đủ mọi lý do để ticket không mất ngữ cảnh (vd vừa đòi gặp người vừa mặc cả)
    reasons: list[str] = []
    if is_human_request:
        reasons.append("HUMAN_REQUEST")
    if is_complaint:
        reasons.append("COMPLAINT")
    if is_discount:
        negotiation = True
        reasons.append("NEGOTIATION")
    if state.get("complexity") == "HIGH" and any(
        i in intents for i in (Intent.KNOWLEDGE.value, Intent.MAINTENANCE.value)
    ):
        reasons.append("COMPLEXITY_HIGH")
    reason = "; ".join(reasons) or None
    route = "human" if reasons else "router"

    return {
        "negotiation_active": negotiation,
        "safety": is_safety,
        "route": route,
        "handover_reason": reason,
        "trace": step(state, "policy_guard"),
    }
