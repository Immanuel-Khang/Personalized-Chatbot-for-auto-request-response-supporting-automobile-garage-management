"""
[TRACK B] Preprocess: tách slot + intent + độ phức tạp. CHỈ ĐỌC, không side effect.
Khi có LLM: dùng structured output (port từ codebase hiện tại).
Fallback: rule-based theo từ khóa.
"""
import re

from app.agent.llm import generate_structured
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent, IntentAndSlotExtraction, ServiceType

# ---- Fallback rule-based ----
KEYWORDS: dict[Intent, list[str]] = {
    Intent.DISCOUNT: ["giảm giá", "bớt", "mặc cả", "khuyến mãi thêm", "ưu đãi thêm", "chiết khấu", "giảm cho"],
    Intent.CONTRACT: ["hợp đồng", "chốt mua", "ký hợp đồng", "đặt cọc"],
    Intent.APPOINTMENT: ["đặt lịch", "đặt hẹn", "lái thử", "hẹn"],
    Intent.MAINTENANCE: ["bảo dưỡng", "bao lâu thay dầu", "mốc km", "số km"],
    Intent.SALES: ["giá", "mua xe", "bao nhiêu", "so sánh", "trả góp", "xe nào", "tư vấn xe"],
    Intent.KNOWLEDGE: ["tại sao", "vì sao", "bao lâu", "khi nào", "dấu hiệu", "đèn", "phanh", "dầu", "lốp"],
}

# Từ khóa gợi ý độ phức tạp cao ngay từ đầu
COMPLEX_SIGNALS = [
    "so sánh chi tiết", "phân tích", "tư vấn toàn bộ", "giải thích kỹ",
    "tại sao lại", "cơ chế hoạt động", "chi tiết kỹ thuật",
]

INTENT_SYSTEM_PROMPT = """\
Classify the customer's latest request into exactly one intent category:
- SALES: Inquiring about car features, models, catalog, prices, or budget-based recommendations.
- MAINTENANCE: Inquiring about service milestones, repair costs, maintenance schedules by mileage.
- KNOWLEDGE: General automotive knowledge questions (why, how, when, symptoms).
- CONTRACT: Ready to purchase, deposit, sign agreement, provide KYC.
- DISCOUNT: Asking for special deals, negotiations, price cuts, discounts.
- APPOINTMENT: Wanting to book/schedule a service, test drive, or visit.
- GREETING: Greetings, thanks, or general hello.
- OTHER: Anything else.

Also extract any relevant customer information from the message:
- car_model: Tên dòng xe (VD: Vios, Cross, Camry)
- budget_vnd: Ngân sách dự kiến (VD: 1500000000 cho "một tỷ rưỡi")
- intended_use: Mục đích mua xe
- current_mileage_km: Số km hiện tại/bảo dưỡng
- customer_name: Họ tên khách hàng
- customer_phone: Số điện thoại
- customer_address: Địa chỉ
- decided_price: Mức giá khách đồng ý chốt (VNĐ)
- service_type: Loại dịch vụ nếu đặt lịch — một trong MAINTENANCE | REPAIR | TEST_DRIVE
- appointment_when: Thời điểm hẹn nếu có (VD: "sáng thứ 3", "ngày mai", "15/10")
"""


def _rule_based_preprocess(state: AgentState) -> dict:
    """Fallback: phân loại intent bằng từ khóa (khi không có LLM)."""
    text = state.get("user_text", "").lower()

    intents = [i.value for i, kws in KEYWORDS.items() if any(k in text for k in kws)]
    if not intents:
        short = len(text.split()) <= 4
        intents = [Intent.GREETING.value] if short and ("chào" in text or "hello" in text) else [Intent.OTHER.value]

    # Đang làm dở appointment flow → tiếp tục
    if intents == [Intent.OTHER.value] and state.get("active_flow") == "appointment":
        intents = [Intent.APPOINTMENT.value]

    slots = dict(state.get("slots", {}))

    # Phone
    if m := re.search(r"\b(0\d{9})\b", text):
        slots["customer_phone"] = m.group(1)

    # Thời gian hẹn
    if m := re.search(r"(thứ\s*[2-7]|chủ nhật|ngày mai|hôm nay|\d{1,2}/\d{1,2})", text):
        when = m.group(1)
        if m2 := re.search(r"(sáng|chiều|tối)", text):
            when += " " + m2.group(1)
        slots["appointment_when"] = when

    # service_type
    if "lái thử" in text or "chạy thử" in text:
        slots["service_type"] = ServiceType.TEST_DRIVE.value
    elif "sửa" in text or "sửa chữa" in text:
        slots["service_type"] = ServiceType.REPAIR.value
    elif "bảo dưỡng" in text:
        slots["service_type"] = ServiceType.MAINTENANCE.value

    # Complexity
    has_complex_signal = any(k in text for k in COMPLEX_SIGNALS)
    complexity = "HIGH" if (has_complex_signal or len(intents) >= 3 or len(text) > 300) else "LOW"
    return {"intents": intents, "slots": slots, "complexity": complexity}


def _llm_preprocess(state: AgentState) -> dict:
    """Dùng LLM structured output để phân loại intent + trích xuất slot."""
    result = generate_structured(
        INTENT_SYSTEM_PROMPT,
        state.get("user_text", ""),
        IntentAndSlotExtraction,
    )
    if result is None:
        return _rule_based_preprocess(state)

    intents = [result.intent.value]

    # Merge slots: giữ slot cũ, chỉ cập nhật trường có giá trị mới
    merged_slots = dict(state.get("slots", {}))
    if result.extracted_slots:
        new_data = result.extracted_slots.model_dump(exclude_unset=True)
        for key, value in new_data.items():
            if value is not None and value != "":
                merged_slots[key] = value

    # Đang làm dở appointment flow → tiếp tục
    if intents == [Intent.OTHER.value] and state.get("active_flow") == "appointment":
        intents = [Intent.APPOINTMENT.value]

    # Complexity ban đầu dựa trên độ dài + tín hiệu rõ ràng;
    # knowledge node sẽ nâng lên HIGH nếu RAG không tìm thấy câu trả lời
    text = state.get("user_text", "")
    has_complex_signal = any(k in text.lower() for k in COMPLEX_SIGNALS)
    complexity = "HIGH" if (has_complex_signal or len(text) > 300) else "LOW"

    return {"intents": intents, "slots": merged_slots, "complexity": complexity}


def preprocess(state: AgentState) -> dict:
    result = _llm_preprocess(state)
    result["trace"] = step(state, "preprocess")
    return result
