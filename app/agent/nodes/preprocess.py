"""
[TRACK B] Preprocess: tách slot + intent + độ phức tạp. CHỈ ĐỌC, không side effect.
Khi có LLM: dùng structured output (port từ codebase hiện tại).
Fallback: rule-based theo từ khóa.
"""
import re

from app.agent.llm import generate_structured
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import CustomerSlots, Intent, IntentAndSlotExtraction, ServiceType

# ---- Fallback rule-based (giữ nguyên từ bộ khung) ----
KEYWORDS: dict[Intent, list[str]] = {
    Intent.DISCOUNT: ["giảm giá", "bớt", "mặc cả", "khuyến mãi thêm", "ưu đãi thêm", "chiết khấu", "giảm cho"],
    Intent.CONTRACT: ["hợp đồng", "chốt mua", "ký hợp đồng", "đặt cọc"],
    Intent.APPOINTMENT: ["đặt lịch", "đặt hẹn", "lái thử", "hẹn"],
    Intent.MAINTENANCE: ["bảo dưỡng", "bao lâu thay dầu", "mốc km", "số km"],
    Intent.SALES: ["giá", "mua xe", "bao nhiêu", "so sánh", "trả góp", "xe nào", "tư vấn xe"],
    Intent.KNOWLEDGE: ["tại sao", "vì sao", "bao lâu", "khi nào", "dấu hiệu", "đèn", "phanh", "dầu", "lốp"],
}

# System prompt cho LLM structured output (port từ codebase hiện tại)
INTENT_SYSTEM_PROMPT = """\
Classify the customer's latest request into exactly one intent category:
- SALES: Inquiring about car features, models, catalog, or prices.
- MAINTENANCE: Inquiring about service milestones, repair costs, maintenance schedules by mileage.
- KNOWLEDGE: General automotive knowledge questions (why, how, when, symptoms).
- CONTRACT: Ready to purchase, deposit, sign agreement, provide KYC.
- DISCOUNT: Asking for special deals, negotiations, price cuts, discounts.
- APPOINTMENT: Wanting to book/schedule a service, test drive, or visit.
- GREETING: Greetings, thanks, or general hello.
- OTHER: Anything else.

Also extract any relevant customer information from the message:
- car_model: Tên dòng xe (VD: Vios, Cross, Camry)
- budget_vnd: Ngân sách dự kiến
- intended_use: Mục đích mua xe
- current_mileage_km: Số km hiện tại/bảo dưỡng
- customer_name: Họ tên khách hàng
- customer_phone: Số điện thoại
- customer_address: Địa chỉ
- decided_price: Mức giá khách đồng ý chốt (VNĐ)
- discount_percent: Phần trăm giảm giá yêu cầu
"""


def _rule_based_preprocess(state: AgentState) -> dict:
    """Fallback: phân loại intent bằng từ khóa (khi không có LLM)."""
    text = state["user_text"].lower()

    intents = [i.value for i, kws in KEYWORDS.items() if any(k in text for k in kws)]
    if not intents:
        short = len(text.split()) <= 4
        intents = [Intent.GREETING.value] if short and ("chào" in text or "hello" in text) else [Intent.OTHER.value]

    # Đang làm dở appointment flow → tiếp tục
    if intents == [Intent.OTHER.value] and state.get("active_flow") == "appointment":
        intents = [Intent.APPOINTMENT.value]

    slots = dict(state.get("slots", {}))
    if m := re.search(r"\b(0\d{9})\b", text):
        slots["phone"] = m.group(1)
    if m := re.search(r"(thứ\s*[2-7]|chủ nhật|ngày mai|hôm nay|\d{1,2}/\d{1,2})", text):
        slots["when_text"] = m.group(1)
        if m2 := re.search(r"(sáng|chiều|tối)", text):
            slots["when_text"] += " " + m2.group(1)
    if "lái thử" in text:
        slots["service_type"] = ServiceType.TEST_DRIVE.value
    elif "sửa" in text:
        slots["service_type"] = ServiceType.REPAIR.value
    elif "bảo dưỡng" in text and Intent.APPOINTMENT.value in intents:
        slots["service_type"] = ServiceType.MAINTENANCE.value

    # Trích xuất % giảm giá từ text
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%", text):
        slots["discount_percent"] = float(m.group(1))

    complexity = "HIGH" if (len(intents) >= 3 or len(text) > 300) else "LOW"
    return {"intents": intents, "slots": slots, "complexity": complexity}


def _llm_preprocess(state: AgentState) -> dict:
    """Dùng LLM structured output để phân loại intent + trích xuất slot (port từ codebase hiện tại)."""
    result = generate_structured(
        INTENT_SYSTEM_PROMPT,
        state["user_text"],
        IntentAndSlotExtraction,
    )
    if result is None:
        return _rule_based_preprocess(state)

    intents = [result.intent.value]

    # Merge slots: giữ slot cũ, chỉ cập nhật trường có giá trị mới (port từ intent_classifier_node)
    merged_slots = dict(state.get("slots", {}))
    if result.extracted_slots:
        new_data = result.extracted_slots.model_dump(exclude_unset=True)
        for key, value in new_data.items():
            if value is not None and value != "":
                merged_slots[key] = value

    # Đang làm dở appointment flow → tiếp tục
    if intents == [Intent.OTHER.value] and state.get("active_flow") == "appointment":
        intents = [Intent.APPOINTMENT.value]

    complexity = "HIGH" if len(state["user_text"]) > 300 else "LOW"
    return {"intents": intents, "slots": merged_slots, "complexity": complexity}


def preprocess(state: AgentState) -> dict:
    result = _llm_preprocess(state)
    result["trace"] = step(state, "preprocess")
    return result
