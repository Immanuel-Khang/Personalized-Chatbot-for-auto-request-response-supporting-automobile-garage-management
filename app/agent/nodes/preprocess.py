"""
[TRACK B] Preprocess: tách intent + task theo vế câu + slot + độ phức tạp + lưu thông tin khách hàng.
CHỈ ĐỌC ngoại trừ việc lưu profile vào DB khi phát hiện tên/SĐT.
Khi có LLM: structured output (intents + tasks). Fallback: rule-based theo từ khóa, tách vế bằng segments.
"""
import logging
import re

from app.agent.llm import generate_structured
from app.agent.nodes.common import step
from app.agent.nodes.router import AFFIRM, CANCEL
from app.agent.nodes.segments import split_clauses
from app.agent.nodes.service_type import detect_component, detect_service_type
from app.agent.state import AgentState
from app.contracts.schemas import Intent, IntentAndSlotExtraction, SalesField, SalesSignals
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
    Intent.SALES: ["giá", "mua xe", "muốn mua", "tìm mua", "tìm một chiếc", "tìm xe", "bao nhiêu", "so sánh",
                   "trả góp", "xe nào", "tư vấn xe", "ngân sách"],
    Intent.KNOWLEDGE: ["tại sao", "vì sao", "bao lâu", "khi nào", "dấu hiệu", "đèn", "phanh", "dầu", "lốp"],
}
NO_TASK = {Intent.GREETING.value, Intent.OTHER.value}
PRICE_QUESTION = ["giá", "bao nhiêu", "so sánh", "trả góp"]
DISCOUNT_PERCENT = re.compile(r"giảm\s*(?:giá\s*)?\d+\s*%")  # "hứa giảm 15% rồi" cũng là mặc cả
NEGATED_BOOKING = re.compile(r"(?:chưa|không)\s+(?:muốn\s+|định\s+|cần\s+)?(?:đặt lịch|đặt hẹn|book)")
BOOKING_SLOT_KEYS = ("service_type", "appointment_when", "service_detail")

# ---- Yêu cầu mua xe: chỉ merge_sales_slots được ghi các slot này ----
SALES_SLOT_KEYS = ("intended_use", "use_category", "budget_vnd", "car_model", "payload_tons")
# Fallback khi không có LLM: vài mẫu câu tổng quát, KHÔNG phải bộ hiểu ngôn ngữ chính
BROWSE_RE = re.compile(r"danh sách|tất cả (?:các )?(?:mẫu|dòng|loại) xe|(?:những|các) (?:mẫu|dòng|loại) xe"
                       r"(?: nào| hiện có| đang có)")
PURPOSE_RE = re.compile(r"(?:\bđể|\bphục vụ)\s+([^,.;?!]+)")  # 'mua xe để chở hàng' → 'chở hàng'
UNSURE_RE = re.compile(r"(?:chưa|không)\s+(?:biết|rõ|chắc)|chưa nghĩ")
BUDGET_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(triệu|tỷ)")
PAYLOAD_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*tấn")
PHONE_RE = re.compile(r"\b0\d{9}\b")
# Không phải câu trả lời cho câu hỏi tư vấn: chào/cảm ơn/từ chối → để router xử lý như bình thường
NOT_AN_ANSWER = ("chào", "cảm ơn", "cám ơn", "thank", "tạm biệt", "thôi", "không cần", "không mua",
                 "không muốn", "để sau")

# Thời điểm hẹn: ngày + giờ + buổi. Thiếu ngày mà lượt trước đã có ngày → ghép ("10 giờ sáng" sau "ngày mai")
DAY_RE = re.compile(r"(thứ\s*[2-7]|chủ nhật|ngày mai|ngày kia|hôm nay|\d{1,2}/\d{1,2})")
HOUR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:giờ|h)(?:\s*(\d{2}))?(?!\w)")
PERIOD_RE = re.compile(r"(sáng|trưa|chiều|tối)")

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
The input may start with context (the question the bot just asked, known purchase requirements) followed by
"Tin nhắn mới của khách". Classify and extract ONLY from the new customer message; quote tasks only from it.
A short reply that answers the bot's pending question about buying a car is SALES.
`sales` fields (vehicle purchase only):
- answers_pending_question: true only when the new message answers the bot's pending question.
- catalog_request: true only when the customer explicitly asks to see the list/all models. "Tôi muốn mua xe" is false.
- intended_use_stated + intended_use: only when the customer says what the vehicle is for (VD "chở hàng",
  "phục vụ gia đình"). Never infer a use from "muốn mua xe". use_category: CARGO | PASSENGER | OTHER.
- uncertain: the customer says they don't know / haven't decided their needs.
- cleared_fields: requirements the customer explicitly drops (VD "không giới hạn ngân sách nữa" → budget_vnd).
  A new value replaces the old one; do not list it in cleared_fields.
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
- payload_tons: Tải trọng hàng cần chở (tấn) nếu khách nói rõ
"""


def classify_clause(text: str) -> set[str]:
    """Intent theo từ khóa cho một vế câu."""
    lowered = text.lower()
    found = {i.value for i, kws in KEYWORDS.items() if any(k in lowered for k in kws)}
    if DISCOUNT_PERCENT.search(lowered):
        found.add(Intent.DISCOUNT.value)
    if NEGATED_BOOKING.search(lowered):  # "chưa định đặt lịch" không phải yêu cầu đặt lịch
        found.discard(Intent.APPOINTMENT.value)
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


def extract_when(text: str, previous: str | None = None) -> str | None:
    """'ngày mai lúc 10 giờ sáng' → 'ngày mai 10 giờ sáng'. Chỉ có giờ → ghép với ngày của lượt trước."""
    lowered = text.lower()
    day, hour, period = DAY_RE.search(lowered), HOUR_RE.search(lowered), PERIOD_RE.search(lowered)
    if not (day or hour):
        return None
    day_text = day.group(1) if day else None
    if not day_text and previous and (prev_day := DAY_RE.search(previous.lower())):
        day_text = prev_day.group(1)
    hour_text = f"{hour.group(1)} giờ" + (f" {hour.group(2)}" if hour.group(2) else "") if hour else None
    return " ".join(p for p in (day_text, hour_text, period.group(1) if period else None) if p)


def _booking_context(state: AgentState, intents: list[str]) -> bool:
    """Chỉ lấp slot đặt lịch khi khách đã muốn đặt (hoặc đang đặt dở / có lịch đặt hoãn chờ).
    Hỏi 'có sửa kính không?' là hỏi thông tin, không phải yêu cầu đặt lịch."""
    offer = state.get("offer") or {}
    return (Intent.APPOINTMENT.value in intents or state.get("active_flow") == "appointment"
            or state.get("pending_intent") == Intent.APPOINTMENT.value
            or offer.get("intent") == Intent.APPOINTMENT.value)


def _keep_old_booking_slots(old: dict, new: dict) -> dict:
    """Bỏ slot đặt lịch vừa trích ngoài ngữ cảnh đặt lịch, giữ nguyên giá trị của lượt trước."""
    gated = dict(new)
    for key in BOOKING_SLOT_KEYS:
        if key in old:
            gated[key] = old[key]
        else:
            gated.pop(key, None)
    return gated


def merge_sales_slots(old: dict, slots: dict, extracted: dict, signals: SalesSignals,
                      pending: str | None) -> tuple[dict, dict]:
    """Ghi yêu cầu mua xe đã kiểm tra vào slots. Trả (slots, sales_signals của lượt).
    - Trường không nhắc tới: giữ giá trị cũ. Giá trị mới hợp lệ: thay giá trị cũ (khách sửa yêu cầu).
    - intended_use chỉ ghi khi khách tự nói (hoặc trả lời đúng câu bot hỏi mục đích); suy đoán bị bỏ.
    - cleared_fields: khách bỏ hẳn yêu cầu. use_category luôn đi cùng intended_use."""
    merged = {k: v for k, v in slots.items() if k not in SALES_SLOT_KEYS}
    merged.update({k: v for k, v in old.items() if k in SALES_SLOT_KEYS})
    answered = signals.answers_pending_question and bool(pending)  # không có câu hỏi chờ thì không có câu trả lời
    cleared = {f.value for f in signals.cleared_fields}
    if SalesField.INTENDED_USE.value in cleared:
        cleared.add("use_category")
    for key in cleared:
        merged.pop(key, None)

    use = extracted.get("intended_use")
    if isinstance(use, str) and use.strip() and (signals.intended_use_stated or (answered and pending == "intended_use")):
        merged["intended_use"] = use.strip()
        merged.pop("use_category", None)  # mục đích mới: nhóm cũ không còn đúng
        if signals.use_category:
            merged["use_category"] = signals.use_category.value
    budget, payload, model = extracted.get("budget_vnd"), extracted.get("payload_tons"), extracted.get("car_model")
    if isinstance(budget, (int, float)) and budget > 0:
        merged["budget_vnd"] = budget
    if isinstance(payload, (int, float)) and 0 < payload < 100:
        merged["payload_tons"] = payload
    model_mentioned = isinstance(model, str) and bool(model.strip())
    if model_mentioned:
        merged["car_model"] = model.strip()
    sales_signals = {"catalog_request": signals.catalog_request, "uncertain": signals.uncertain,
                     "answered": answered, "model_mentioned": model_mentioned}
    return merged, sales_signals


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _rule_sales_signals(state: AgentState, raw: str, intents: list[str]) -> tuple[dict, SalesSignals]:
    """Fallback không có LLM: hiểu câu trả lời ngắn nhờ câu hỏi bot đang chờ (sales_question), xin xem danh mục,
    'mua xe để ...', ngân sách/tải trọng có số. Không suy ra nhóm mục đích (use_category)."""
    text = raw.lower().strip()
    bare = text.rstrip(".!?, ")
    pending = (state.get("sales_question") or {}).get("asked")
    signals, extracted = SalesSignals(), {}
    # Câu trả lời cho câu hỏi chờ: không có intent khác, không phải chào/cảm ơn/hủy/SĐT, không có flow ghi dữ liệu
    answers = (bool(pending) and not (state.get("active_flow") or state.get("pending_intent"))
               and set(intents) <= {Intent.OTHER.value, Intent.SALES.value}
               and not any(k in text for k in CANCEL + list(NOT_AN_ANSWER)) and not PHONE_RE.search(text))
    if answers and bare in AFFIRM:  # "Có" chỉ rõ nghĩa khi bot vừa hỏi có muốn xem danh sách không
        answers = signals.catalog_request = pending == "browse_offer"
    if BROWSE_RE.search(text) and "xe" in text:
        signals.catalog_request = True
    if not (answers or signals.catalog_request or Intent.SALES.value in intents):
        return extracted, signals
    if UNSURE_RE.search(text):
        signals.uncertain = True
    elif m := PURPOSE_RE.search(text):
        extracted["intended_use"], signals.intended_use_stated = m.group(1).strip(), True
    elif answers and pending == "intended_use" and not signals.catalog_request:
        extracted["intended_use"] = raw.strip().rstrip(".!?")  # bot vừa hỏi mục đích: cả câu là câu trả lời
    if m := BUDGET_RE.search(text):
        extracted["budget_vnd"] = _number(m.group(1)) * (1e6 if m.group(2) == "triệu" else 1e9)
    if m := PAYLOAD_RE.search(text):
        extracted["payload_tons"] = _number(m.group(1))
    signals.answers_pending_question = answers
    return extracted, signals


def _ensure_sales_task(result: dict, raw: str) -> None:
    """Khách trả lời câu hỏi mua xe / xin xem danh mục mà chưa có task SALES → thêm task bằng code, router định tuyến."""
    signals = result["sales_signals"]
    if not (signals["answered"] or signals["catalog_request"]):
        return
    if not any(t["intent"] == Intent.SALES.value for t in result["tasks"]):
        result["tasks"].append({"intent": Intent.SALES.value, "text": raw})
    intents = [i for i in result["intents"] if i != Intent.OTHER.value]
    result["intents"] = list(dict.fromkeys(intents + [Intent.SALES.value]))


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
    # Câu hỏi thuần ("Phanh kêu có nguy hiểm không?") không phải trả lời slot: không ghi đè loại dịch vụ đang đặt
    question_only = (Intent.APPOINTMENT.value not in intents
                     and any(i in intents for i in (Intent.KNOWLEDGE.value, Intent.SALES.value, Intent.AFTER_SALES.value)))
    if _booking_context(state, intents):
        if not question_only and (when := extract_when(text, old_slots.get("appointment_when"))):
            slots["appointment_when"] = when
        if not question_only and (service := detect_service_type(text).service):  # mơ hồ → appointment hỏi lại
            slots["service_type"] = service.value
        if not question_only and (component := detect_component(text)):  # khách đổi hạng mục (kính → điều hòa)
            slots["service_detail"] = component

    extracted, signals = _rule_sales_signals(state, raw, intents)
    pending = (state.get("sales_question") or {}).get("asked")
    slots, sales_signals = merge_sales_slots(old_slots, slots, extracted, signals, pending)
    result = {"intents": intents, "tasks": tasks, "slots": slots, "sales_signals": {**sales_signals, "source": "rules"},
              "slots_updated": _slots_updated(old_slots, slots), "complexity": _complexity(raw)}
    _ensure_sales_task(result, raw)
    return result


def _llm_preprocess(state: AgentState) -> dict:
    """Dùng LLM structured output để phân loại intent, tách task và trích xuất slot."""
    text = state.get("user_text", "")
    message, with_context = _llm_input(state, text)
    result = generate_structured(INTENT_SYSTEM_PROMPT, message, IntentAndSlotExtraction)
    if result is None:
        return _rule_based_preprocess(state)

    def task_text(t) -> str:
        quoted = t.text.strip()
        # Có ngữ cảnh: phần câu phải trích từ tin nhắn mới, không lấy câu hỏi/ngữ cảnh của bot
        return quoted if quoted and (not with_context or quoted.lower() in text.lower()) else text

    tasks = [{"intent": t.intent.value, "text": task_text(t)}
             for t in result.tasks if t.intent.value not in NO_TASK]
    intents = list(dict.fromkeys([i.value for i in result.intents] + [t["intent"] for t in tasks]))
    intents = intents or [Intent.OTHER.value]
    if not tasks:  # model không tách vế → mỗi intent dùng cả câu
        tasks = [{"intent": i, "text": text} for i in intents if i not in NO_TASK]

    # Merge slots: giữ slot cũ, chỉ cập nhật trường có giá trị mới
    old_slots = dict(state.get("slots", {}))
    merged_slots = dict(old_slots)
    extracted = result.extracted_slots.model_dump(exclude_unset=True) if result.extracted_slots else {}
    for key, value in extracted.items():
        if value is not None and value != "" and key not in SALES_SLOT_KEYS:
            merged_slots[key] = value
    if not _booking_context(state, intents):
        merged_slots = _keep_old_booking_slots(old_slots, merged_slots)
    pending = (state.get("sales_question") or {}).get("asked")
    merged_slots, sales_signals = merge_sales_slots(old_slots, merged_slots, extracted,
                                                    result.sales or SalesSignals(), pending)

    # Policy Guard xử lý complexity HIGH trước khi router lập kế hoạch
    output = {"intents": intents, "tasks": tasks, "slots": merged_slots, "sales_signals": {**sales_signals, "source": "llm"},
              "slots_updated": _slots_updated(old_slots, merged_slots), "complexity": _complexity(text)}
    _ensure_sales_task(output, text)
    return output


def _llm_input(state: AgentState, text: str) -> tuple[str, bool]:
    """Tin nhắn gửi LLM: chỉ thêm ngữ cảnh có cấu trúc (câu bot đang chờ trả lời, yêu cầu mua xe đã biết),
    không gửi cả lịch sử hay catalog. Trả (nội dung, có ngữ cảnh không)."""
    pending = state.get("sales_question")
    known = {k: v for k, v in state.get("slots", {}).items() if k in SALES_SLOT_KEYS}
    if not pending and not known:
        return text, False
    lines = []
    if pending:
        lines.append(f"Câu bot vừa hỏi (đang chờ khách trả lời): {pending.get('question', '')}")
    if known:
        lines.append(f"Yêu cầu mua xe đã biết: {known}")
    return "\n".join(lines) + f"\n\nTin nhắn mới của khách:\n{text}", True


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
