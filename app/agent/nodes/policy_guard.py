"""
[TRACK B] Policy Guard: NƠI DUY NHẤT quyết định chính sách (mặc cả/giảm giá, đòi gặp người, khiếu nại, an toàn).
Port từ codebase hiện tại: thêm logic xử lý discount (auto-approve ≤5%, pending >5%).
"""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import Intent
from app.services import get_services

HUMAN_REQUEST = ["gặp nhân viên", "nói chuyện với người", "gặp người thật", "tư vấn viên"]
COMPLAINT = ["khiếu nại", "phàn nàn", "tệ quá", "không hài lòng", "lừa đảo"]
SAFETY = ["mất phanh", "phanh không ăn", "cháy", "khói", "đèn dầu sáng", "đèn áp suất dầu"]


def policy_guard(state: AgentState) -> dict:
    text = state["user_text"].lower()
    intents = state.get("intents", [])
    slots = state.get("slots", {})

    negotiation = state.get("negotiation_active", False)
    safety = any(k in text for k in SAFETY)
    discount_auto_approved = False
    pending_discount_id = state.get("pending_discount_id")

    reason, route = None, "router"

    # --- Discount / Negotiation logic (port từ codebase hiện tại) ---
    if Intent.DISCOUNT.value in intents:
        discount_percent = slots.get("discount_percent")
        car_model = slots.get("car_model", "")

        if discount_percent is not None and car_model:
            # Có đủ thông tin → gọi DiscountService
            result = get_services().discount.request_discount(
                session_id=str(state.get("conversation_id", "unknown")),
                car_name=car_model,
                percent=discount_percent,
                max_auto_approve=settings.max_discount_auto_approve,
            )
            if result.status == "AUTO_APPROVED":
                # ≤ ngưỡng → bot tự duyệt, cho đi tiếp qua router → sales sẽ xử lý
                discount_auto_approved = True
                route = "router"
                # Lưu thông tin giảm giá vào slots để sales node biết
                slots = dict(slots)
                slots["discount_result_status"] = result.status
                slots["discount_result_message"] = result.message
                slots["discounted_price"] = result.discounted_price
                slots["discount_percent_applied"] = result.discount_percent
            else:
                # > ngưỡng → cần quản lý duyệt → chuyển người
                negotiation = True
                pending_discount_id = result.request_id
                reason = f"DISCOUNT_{result.discount_percent}%_PENDING"
                route = "human"
        elif discount_percent is not None and not car_model:
            # Có % nhưng không rõ xe → hỏi lại qua router (sales sẽ hỏi)
            route = "router"
        else:
            # Chỉ hỏi chung về giảm giá mà không nêu con số cụ thể → chuyển người
            negotiation = True
            reason = "NEGOTIATION"
            route = "human"
    elif negotiation and Intent.DISCOUNT.value not in intents:
        # Đang trong trạng thái negotiation nhưng câu mới không phải discount
        # → nếu hỏi gì khác thì cho qua (sticky nhưng không block)
        pass

    # --- Các rule cứng khác ---
    if route == "router":
        if any(k in text for k in HUMAN_REQUEST):
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
        "pending_discount_id": pending_discount_id,
        "discount_auto_approved": discount_auto_approved,
        "slots": slots,
        "trace": step(state, "policy_guard"),
    }
