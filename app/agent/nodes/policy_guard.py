"""
[TRACK B] Policy Guard: NƠI DUY NHẤT quyết định chính sách (mặc cả/giảm giá, đòi gặp người, khiếu nại, an toàn).
Bot KHÔNG có quyền duyệt giảm giá: mọi yêu cầu mặc cả/giảm giá đều chuyển người.
negotiation_active là cờ sticky (đọc/ghi bảng conversations): đã bật thì giữ.

Thứ tự ưu tiên:
  1. Safety → route = knowledge (để knowledge thêm cảnh báo hotline)
  2. Yêu cầu gặp người thật → human
  3. Khiếu nại / không hài lòng → human
  4. Intent DISCOUNT (bot không có quyền giảm giá) → human
  5. Mặc định → tiếp tục router
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

    # Mặc định: tiếp tục xử lý bình thường
    reason: str | None = None
    route = "router"

    # Safety → chuyển sang knowledge để thêm cảnh báo + hotline
    if is_safety:
        route = "knowledge"

    # Các trigger chuyển người (ghi đè safety nếu cùng xuất hiện)
    if is_human_request:
        reason, route = "HUMAN_REQUEST", "human"
    elif is_complaint:
        reason, route = "COMPLAINT", "human"
    elif is_discount:
        negotiation = True
        reason, route = "NEGOTIATION", "human"

    return {
        "negotiation_active": negotiation,
        "safety": is_safety,
        "route": route,
        "handover_reason": reason,
        "trace": step(state, "policy_guard"),
    }
