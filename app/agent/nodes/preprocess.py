"""
[TRACK B] Preprocess: tách intent + task theo vế câu + slot + độ phức tạp + lưu thông tin khách hàng.
CHỈ ĐỌC ngoại trừ việc lưu profile vào DB khi phát hiện tên/SĐT.
Khi có LLM: structured output (intents + tasks). Fallback: rule-based theo từ khóa, tách vế bằng segments.
"""
import logging
import re

from app.agent.llm import generate_structured
from app.agent.nodes.common import step
from app.agent.nodes.segments import split_clauses
from app.agent.nodes.service_type import detect_service_type
from app.agent.state import AgentState
from app.contracts.schemas import Intent, IntentAndSlotExtraction
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# ---- Fallback rule-based ----
KEYWORDS: dict[Intent, list[str]] = {
    Intent.DISCOUNT: ["giảm giá", "bớt", "mặc cả", "khuyến mãi thêm", "ưu đãi thêm", "chiết khấu", "giảm cho"],
    Intent.CONTRACT: ["hợp đồng", "chốt mua", "ký hợp đồng", "đặt cọc"],
    Intent.APPOINTMENT: ["đặt lịch", "đặt hẹn", "lái thử", "chạy thử", "hẹn", "mang xe qua", "mang xe đến", "book",
                         "đến showroom", "ghé showroom", "qua showroom", "tới showroom", "xem xe trực tiếp"],
    Intent.AFTER_SALES: ["lịch sử bảo dưỡng", "lịch sử sửa chữa", "lịch sử dịch vụ", "lần trước", "đã sửa gì", "đã làm gì"],
    Intent.MAINTENANCE: ["bảo dưỡng", "bao lâu thay dầu", "mốc km", "số km"],
    Intent.SALES: ["giá", "mua xe", "bao nhiêu", "so sánh", "trả góp", "xe nào", "tư vấn xe"],
    Intent.KNOWLEDGE: ["tại sao", "vì sao", "bao lâu", "khi nào", "dấu hiệu", "đèn", "phanh", "dầu", "lốp"],
}
NO_TASK = {Intent.GREETING.value, Intent.OTHER.value}
PRICE_QUESTION = ["giá", "bao nhiêu", "so sánh", "trả góp"]

# Từ khóa gợi ý độ phức tạp cao ngay từ đầu
COMPLEX_SIGNALS = [
    "so sánh chi tiết", "phân tích", "tư vấn toàn bộ", "giải thích kỹ",
    "tại sao lại", "cơ chế hoạt động", "chi tiết kỹ thuật",
]

INTENT_SYSTEM_PROMPT = """\
Classify the customer's latest request into ALL applicable intent categories.
Return intents in order of importance, with the main request first.
Also return `tasks`: one item per intent, where `text` quotes the exact part of the message for that intent.
A price question plus a test drive request has both SALES and APPOINTMENT, each with its own text.
A technical question plus a visit request has both KNOWLEDGE and APPOINTMENT.
Wanting to visit the showroom to see a car is APPOINTMENT; add SALES only if they ask about price or specs.
A booking request mentioning maintenance as the service is APPOINTMENT, not a separate MAINTENANCE question.
A service history request is AFTER_SALES, not a separate MAINTENANCE question.
Asking how to contact the garage (hotline, address) is OTHER, not APPOINTMENT.
Only classify APPOINTMENT when the customer clearly asks to book/visit. If the message is unclear, garbled,
or you are not sure what the customer wants, return OTHER and do NOT extract service_type or appointment_when.
Never guess slot values: only extract a field when the customer states it explicitly.
- SALES: Inquiring about car features, models, catalog, prices, or budget-based recommendations.
- MAINTENANCE: Inquiring about service milestones, repair costs, maintenance schedules by mileage.
- KNOWLEDGE: General automotive knowledge questions (why, how, when, symptoms).
- CONTRACT: Ready to purchase, deposit, sign agreement, provide KYC.
- DISCOUNT: Asking for special deals, negotiations, price cuts, discounts.
- APPOINTMENT: Wanting to book/schedule a service, test drive, or showroom visit.
- AFTER_SALES: Asking about previous service history, repairs, or work already done.
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
- service_type: Loại dịch vụ nếu đặt lịch — một trong MAINTENANCE | REPAIR | TEST_DRIVE | SHOWROOM_VISIT
  (SHOWROOM_VISIT = đến showroom xem xe trực tiếp)
- appointment_when: Thời điểm hẹn nếu có (VD: "sáng thứ 3", "ngày mai", "15/10")
"""


def classify_clause(text: str) -> set[str]:
    """Intent theo từ khóa cho một vế câu."""
    lowered = text.lower()
    found = {i.value for i, kws in KEYWORDS.items() if any(k in lowered for k in kws)}
    if Intent.AFTER_SALES.value in found:  # 'lịch sử bảo dưỡng' không phải câu hỏi bảo dưỡng
        found -= {Intent.MAINTENANCE.value, Intent.KNOWLEDGE.value}
    # 'chốt mua xe' là hợp đồng; chỉ thêm SALES khi vế đó thật sự hỏi giá
    if Intent.CONTRACT.value in found and not any(k in lowered for k in PRICE_QUESTION):
        found.discard(Intent.SALES.value)
    return found


def _complexity(text: str) -> str:
    has_complex_signal = any(k in text.lower() for k in COMPLEX_SIGNALS)
    return "HIGH" if (has_complex_signal or len(text) > 300) else "LOW"


def _slots_updated(old: dict, new: dict) -> bool:
    return any(old.get(k) != v for k, v in new.items())


def _rule_based_preprocess(state: AgentState) -> dict:
    """Fallback: phân loại intent bằng từ khóa trên từng vế câu (khi không có LLM)."""
    raw = state.get("user_text", "")
    text = raw.lower()
    order = [i.value for i in KEYWORDS]

    tasks = [{"intent": i, "text": clause}
             for clause, found in split_clauses(raw, classify_clause)
             for i in sorted(found, key=order.index)]
    intents = list(dict.fromkeys(t["intent"] for t in tasks))
    if not intents:
        short = len(text.split()) <= 4
        intents = [Intent.GREETING.value] if short and ("chào" in text or "hello" in text) else [Intent.OTHER.value]

    old_slots = dict(state.get("slots", {}))
    slots = dict(old_slots)
    if m := re.search(r"\b(0\d{9})\b", text):
        slots["customer_phone"] = m.group(1)
    if m := re.search(r"(thứ\s*[2-7]|chủ nhật|ngày mai|hôm nay|\d{1,2}/\d{1,2})", text):
        when = m.group(1)
        if m2 := re.search(r"(sáng|chiều|tối)", text):
            when += " " + m2.group(1)
        slots["appointment_when"] = when
    # Câu hỏi thuần ("Phanh kêu có nguy hiểm không?") không phải trả lời slot: không ghi đè loại dịch vụ đang đặt
    question_only = (Intent.APPOINTMENT.value not in intents
                     and any(i in intents for i in (Intent.KNOWLEDGE.value, Intent.SALES.value, Intent.AFTER_SALES.value)))
    if not question_only and (service := detect_service_type(text).service):  # mơ hồ → appointment hỏi lại
        slots["service_type"] = service.value

    return {"intents": intents, "tasks": tasks, "slots": slots,
            "slots_updated": _slots_updated(old_slots, slots), "complexity": _complexity(raw)}


def _llm_preprocess(state: AgentState) -> dict:
    """Dùng LLM structured output để phân loại intent, tách task và trích xuất slot."""
    text = state.get("user_text", "")
    result = generate_structured(INTENT_SYSTEM_PROMPT, text, IntentAndSlotExtraction)
    if result is None:
        return _rule_based_preprocess(state)

    tasks = [{"intent": t.intent.value, "text": t.text.strip() or text}
             for t in result.tasks if t.intent.value not in NO_TASK]
    intents = list(dict.fromkeys([i.value for i in result.intents] + [t["intent"] for t in tasks]))
    intents = intents or [Intent.OTHER.value]
    if not tasks:  # model không tách vế → mỗi intent dùng cả câu
        tasks = [{"intent": i, "text": text} for i in intents if i not in NO_TASK]

    # Merge slots: giữ slot cũ, chỉ cập nhật trường có giá trị mới
    old_slots = dict(state.get("slots", {}))
    merged_slots = dict(old_slots)
    if result.extracted_slots:
        for key, value in result.extracted_slots.model_dump(exclude_unset=True).items():
            if value is not None and value != "":
                merged_slots[key] = value

    # Policy Guard xử lý complexity HIGH trước khi router lập kế hoạch
    return {"intents": intents, "tasks": tasks, "slots": merged_slots,
            "slots_updated": _slots_updated(old_slots, merged_slots), "complexity": _complexity(text)}


def _extract_name(text: str) -> str | None:
    """Trích tên khách hàng từ câu nói (tôi tên X, tôi là X)."""
    patterns = [
        r"(?:tên\s+(?:tôi|mình|em)\s+(?:là\s+)?|(?:tôi|mình|em)\s+tên\s+)(\w+)",
        r"(?:tôi|mình|em)\s+là\s+(\w+)",
    ]
    for p in patterns:
        if m := re.search(p, text, re.IGNORECASE):
            return m.group(1).strip()
    return None


def _save_profile_to_db(session_id: str, name: str | None, phone: str | None) -> None:
    """Lưu thông tin khách hàng vào DB nếu phát hiện tên hoặc SĐT."""
    if not name and not phone:
        return

    db = SessionLocal()
    try:
        # Tìm hoặc tạo chat_session
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not chat_session:
            chat_session = ChatSession(id=session_id)
            db.add(chat_session)
            db.flush()

        # Tìm hoặc tạo customer
        customer = None
        if chat_session.customer_id:
            customer = db.query(Customer).filter(Customer.id == chat_session.customer_id).first()

        if not customer:
            # Tạo customer mới
            customer = Customer()
            db.add(customer)
            db.flush()
            # Link customer với chat_session
            chat_session.customer_id = customer.id

        # Cập nhật thông tin
        if name and not customer.full_name:
            customer.full_name = name
        if phone and not customer.phone_number:
            customer.phone_number = phone

        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Error saving profile: %s", e)
    finally:
        db.close()


def preprocess(state: AgentState) -> dict:
    result = _llm_preprocess(state)

    # Lưu profile vào DB nếu phát hiện tên hoặc SĐT
    text = state.get("user_text", "")
    name = _extract_name(text)
    phone = result["slots"].get("customer_phone")
    session_id = state.get("session_id")
    if session_id and (name or phone):
        _save_profile_to_db(session_id, name, phone)

    result["trace"] = step(state, "preprocess")
    return result
