"""Preprocessing Agent: one structured LLM interpretation of the current message.

It performs the six preprocessing jobs (contextual intent, task identification,
slot extraction, reference resolution, ambiguity/conflict detection, structured
output) and then *validates* the model output in application code:
- slot keys must be canonical and backed by current-turn evidence;
- a SET over an existing different value becomes a CORRECT (latest wins);
- a car reference must be one of the cars the bot listed (car_list).
Downstream nodes read this result; none of them re-parse the Vietnamese text.
Preprocessing has no side effects other than writing the debug trace.
"""
import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path

from app.agent.date_extraction import sync_date_fact
from app.agent.llm import generate_structured
from app.agent.nodes.input import AVAILABLE_ACTIONS, build_context
from app.agent.state import AgentState, fact_value
from app.identity import normalize_phone, is_full_name
from app.services import get_services
from app.contracts.schemas import (
    ConversationInterpretation,
    FactSource,
    Intent,
    KnownFact,
    ServiceType,
    SlotOperation,
    SlotUpdate,
    TaskInterpretation,
    TaskRelationship,
)

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)\d{9}(?!\d)")

CANONICAL_SLOTS = {
    "budget_vnd": "Ngân sách tối đa, số nguyên VND (500 triệu -> 500000000)",
    "intended_use": "Mục đích dùng xe (chở hàng, chở người, xe ben...)",
    "payload_tons": "Tải trọng mong muốn (tấn, số)",
    "car_model": "Tên mẫu/dòng xe khách nói rõ",
    "service_type": ("Loại lịch hẹn: MAINTENANCE (bảo dưỡng) | REPAIR (sửa chữa) | "
                     "SHOWROOM_VISIT (xem xe tại showroom) | CONTRACT_SIGN (đến ký hợp đồng mua xe). "
                     "Xưởng KHÔNG có dịch vụ lái thử"),
    "service_detail": "Hạng mục/bộ phận cần xử lý (kính, phanh, điều hòa...)",
    "appointment_when": "Ngày giờ hẹn đúng như khách nói (VD 'sáng thứ 7 tuần sau')",
    "customer_phone": "Số điện thoại khách",
    "customer_name": ("Tên khách tự xưng, viết chuẩn có dấu và hoa chữ cái đầu (dương định khang -> Dương Định Khang); "
                      "chỉ phần tên khách nói, có thể chỉ tên gọi; không kèm xưng hô anh/chị/em"),
    "customer_address": "Địa chỉ khách",
    "decided_price": "Giá chốt hợp đồng (VND)",
    "current_mileage_km": "Số km hiện tại của xe (số)",
}
# Values that are usually paraphrased/normalised by the model (e.g. "500 triệu" -> 500000000).
_NORMALISED_VALUE_KEYS = {"appointment_when", "service_type", "service_detail", "budget_vnd", "customer_name",
                          "decided_price", "payload_tons", "current_mileage_km", "intended_use", "customer_address"}

INTERPRETATION_PROMPT = """\
Bạn là Preprocessing Agent của trợ lý gara ô tô. Phân tích TIN NHẮN MỚI NHẤT của khách trong ngữ cảnh hội thoại
(tóm tắt + các tin gần nhất) và CONVERSATION STATE, trả về đúng schema ConversationInterpretation. Các node sau chỉ
đọc kết quả của bạn, không đọc lại câu chữ.

1. Task: mỗi yêu cầu riêng biệt là một task, theo thứ tự xuất hiện; chỉ phân tích yêu cầu MỚI, không lặp task cũ.
   "Tìm xe số 1 và đặt lịch xem xe" = SALES product_detail + APPOINTMENT book_appointment.
   - Xem xe tại showroom là APPOINTMENT. Thông số/giá của một mẫu trong catalog ("động cơ của FUSO Canter FI170",
     "xe số 2 hộp số mấy cấp") là SALES product_specs; KNOWLEDGE chỉ cho câu hỏi kỹ thuật chung, sửa chữa, an toàn.
   - Chọn một xe trong danh sách bot vừa đưa ("mình chọn mẫu đầu", "lấy xe số 2") cũng là SALES purchase_interest,
     đặt car_model bằng cách đếm trong danh sách.
   - Chỉ nói muốn mua ("tôi muốn mua xe này", "chốt mẫu này") mà chưa yêu cầu làm/ký hợp đồng hay đưa thông tin hợp
     đồng: SALES purchase_interest (đặt car_model nếu xe đã rõ). CONTRACT start_contract chỉ khi khách nói rõ muốn
     làm/ký hợp đồng, đồng ý bot đề nghị làm hợp đồng, hoặc cung cấp thông tin cho workflow hợp đồng đang mở.
   - Mặc cả, xin giảm giá, xin quà: DISCOUNT.
   - Chỉ HỎI xưởng có làm dịch vụ X không, hoặc hỏi thủ tục/giấy tờ, chưa yêu cầu đặt lịch/làm hợp đồng: inquiry_only=true.
2. relationship của task theo ngữ cảnh:
   - Trả lời câu bot vừa hỏi: ANSWER_TO_QUESTION, mang domain/action của việc đang làm (không dùng OTHER); đặt
     referenced_interaction_id nếu khớp một pending_interactions.
   - Đồng ý/từ chối gợi ý hay câu xác nhận của bot (kể cả xác nhận đổi/hủy lịch): ACCEPT_OFFER / REJECT_OFFER
     (accepts_offer=true/false), KHÔNG phải CANCEL_TASK.
   - Sửa thông tin đã nói: CORRECT_PREVIOUS_INFO + slot_update CORRECT. Nói tiếp workflow: CONTINUE_TASK. Bỏ việc cũ
     làm việc khác: CHANGE_TOPIC. Hủy việc đang làm dở: CANCEL_TASK.
   - Lịch hẹn ĐÃ ĐẶT: "hủy lịch hẹn của tôi" -> cancel_appointment (NEW_REQUEST); "đổi lịch sang ngày mai" ->
     reschedule_appointment + slot_update appointment_when; "lịch hẹn của tôi lúc mấy giờ", "đã xác nhận chưa" ->
     appointment_details.
3. Slot: chỉ key trong canonical_slots, chỉ khi khách nói rõ trong tin MỚI (hoặc đáp ngắn cho câu bot hỏi đúng slot đó).
   evidence là đoạn nguyên văn trong tin mới. Không suy đoán.
   - Task APPOINTMENT mà tin nêu loại lịch -> BẮT BUỘC có slot_update service_type (evidence là cụm đó): "xem xe",
     "đến showroom" -> SHOWROOM_VISIT; "bảo dưỡng"; "sửa" -> REPAIR; "ký hợp đồng" -> CONTRACT_SIGN. "Lái thử": xưởng
     không có, KHÔNG ghi service_type.
   - appointment_when: tin mới có ngày/giờ hẹn -> BẮT BUỘC có slot_update; value là TOÀN BỘ thời gian hẹn hiện tại theo
     lời khách, không đổi sang ngày cụ thể. Khách chỉ bổ sung phần cho thời gian đã có trong known_facts (đã có "ngày
     mai", nói thêm "lúc 8 giờ" hoặc "sáng") -> value gộp ("sáng ngày mai lúc 8 giờ"), evidence chỉ là đoạn trong tin
     mới. Chưa có giờ cụ thể vẫn ghi ("chiều thứ 2 tuần sau"); workflow tự hỏi giờ. KHÔNG tạo khi tin mới không nêu
     thời gian (VD "đúng rồi" xác nhận một đề xuất). Bot vừa hỏi ngày giờ ký hợp đồng mà khách đáp thời gian ->
     APPOINTMENT book_appointment ANSWER_TO_QUESTION, không phải CONTRACT.
   - customer_phone, customer_address, decided_price: tin mới có thông tin nào thì BẮT BUỘC có slot_update riêng cho
     từng thông tin đó, kể cả khi khách đưa nhiều thông tin trong một tin hoặc đáp câu bot vừa xin. evidence là đoạn
     nguyên văn khách gõ (giữ cả lỗi chính tả). customer_phone: value đúng như khách gõ. customer_address: cả địa
     chỉ khách nói. decided_price: số nguyên VND; khách nói chốt theo "giá mặc định/giá niêm yết/giá của xe" thì
     value là giá niêm yết của xe đó, lấy từ tin gần nhất của bot (không đoán, không thấy giá thì bỏ qua).
   - customer_name: chỉ khi khách tự giới thiệu hoặc đáp câu hỏi tên; khách chỉ nói tên gọi thì ghi đúng phần đó,
     KHÔNG tự thêm họ. Xưng hô, lời chào, tên xe, địa danh không phải tên.
4. Xe khách đang đếm:
   - presented_cars (điền TRƯỚC): nếu tin GẦN NHẤT của bot (không phải của khách) giới thiệu một hoặc nhiều xe (một xe
     cũng là danh sách một phần tử), ghi TÊN TỪNG XE theo thứ tự các dòng/mục, vì khách đếm "mẫu số N" theo đó; dòng ghi hai xe ("FUSO FI170 và FI170L") là hai phần tử
     liên tiếp; xe chỉ nhắc trong câu dẫn ("Ngoài X, gồm:") thì KHÔNG ghi. VD "Ngoài ISUZU A, gồm: - FUSO B và FUSO C -
     THACO D" -> ["FUSO B", "FUSO C", "THACO D"]. Tin đó không đưa xe nào -> để trống (danh sách cũ được giữ).
   - Tham chiếu "xe số 1", "mẫu thứ ba", "nó", "xe đó": đặt car_model của task liên quan là TÊN XE khách chỉ, BẰNG CÁCH
     ĐẾM trong presented_cars vừa ghi (nếu trống thì car_list trong CONVERSATION STATE); mỗi task lấy đúng xe nó nói
     tới. VD "xe số 2" với ["FUSO B", "FUSO C"] -> "FUSO C". Không có xe đó hoặc không xác định duy nhất -> null.
   - car_model của task chỉ khi TIN MỚI nêu tên xe, đếm số thứ tự, hoặc "xe này/nó" mà tin gần nhất của bot nói về
     đúng MỘT xe. Tin mới bắt đầu việc mới mà không nêu xe ("muốn mua xe", "đặt lịch bảo dưỡng"), hoặc "xe này" khi
     bot vừa đưa NHIỀU xe, hoặc khi tin gần nhất của bot không nói về xe nào -> null.
     Ngoại lệ: task CONTRACT (khách muốn làm/ký hợp đồng hoặc đang gửi thông tin hợp đồng) mà bot đã ghi nhận khách
     chọn đúng một xe -> car_model là xe đó. car_list và known_facts là xe khách
     từng xem hay việc đã xong, KHÔNG tự lấy làm xe của việc mới, kể cả khi danh sách chỉ có một xe.
5. Mơ hồ: thiếu thông tin workflow sẽ tự hỏi (SĐT, ngày giờ) không phải mơ hồ. Không hiểu tin nhắn hoặc có điểm mơ hồ
   chặn task: uncertain=true và một task general_conversation requires_clarification=true.
6. signals: wants_human (muốn gặp nhân viên), complaint (phàn nàn, bức xúc), safety_risk (xe mất phanh, cháy, khói, mất
   lái...), discount_request, deal_change (muốn sửa điều khoản hợp đồng, thương lượng lại giá, hỏi phí phát sinh của hợp
   đồng; KHÔNG bật khi khách chỉ trả lời giá chốt bot hỏi hay "giá/phí" nằm trong tên riêng). complexity=HIGH chỉ khi
   câu hỏi kỹ thuật vượt quá tư vấn thông thường (chẩn đoán phức tạp); nhiều yêu cầu thông thường, lời chào, hỏi
   giá/thông số -> LOW.
"""


def _action_line(item: dict) -> str:
    required = f" [bắt buộc: {', '.join(item['required_fields'])}]" if item.get("required_fields") else ""
    return f"- {item['domain']} {item['action']}: {item['description']}{required}"


# Static part, identical every turn (a stable prefix also lets the provider cache it).
SYSTEM_PROMPT = (f"{INTERPRETATION_PROMPT}\ncanonical_slots (key: ý nghĩa):\n"
                 + "\n".join(f"- {key}: {desc}" for key, desc in CANONICAL_SLOTS.items())
                 + "\n\navailable_actions (domain action: mô tả):\n"
                 + "\n".join(_action_line(item) for item in AVAILABLE_ACTIONS))


def _context_prompt(state: AgentState) -> str:
    # History and current text go to the LLM as chat messages (llm.py); only the per-turn state goes here,
    # and empty parts are left out.
    context = build_context(state, CANONICAL_SLOTS)
    dynamic = context.model_dump(mode="json", exclude_none=True,
                                 include={"pending_interactions", "active_workflow", "car_list"})
    # appointment_date is derived in code from appointment_when; the model only sees the customer's words.
    dynamic["known_facts"] = {key: fact.value for key, fact in context.known_facts.items()
                              if key != "appointment_date"}
    dynamic = {key: value for key, value in dynamic.items() if value}
    return f"{SYSTEM_PROMPT}\n\nCONVERSATION STATE JSON:\n{json.dumps(dynamic, ensure_ascii=False, default=str)}"


def _neutral_interpretation(text: str, reason: str) -> ConversationInterpretation:
    """Safe result when the LLM is unavailable: no business action, no facts."""
    task = TaskInterpretation(
        task_id=f"task_{uuid.uuid4().hex[:8]}", domain=Intent.OTHER, action="general_conversation",
        text=text, relationship=TaskRelationship.UNCLEAR, requires_clarification=True)
    return ConversationInterpretation(message_summary=f"Structured interpretation unavailable: {reason}",
                                      tasks=[task], uncertain=True)


def _normalise(value: str) -> str:
    # NFC: the model may return Vietnamese accents in another Unicode form than the customer typed.
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _evidence_is_current(update: SlotUpdate, text: str) -> bool:
    """Reject fabricated evidence before a model-proposed fact enters state."""
    current = _normalise(text)
    if not update.evidence.strip() or _normalise(update.evidence) not in current:
        return False
    if update.operation == SlotOperation.CLEAR:
        return True
    return update.value is not None and (
        update.key in _NORMALISED_VALUE_KEYS or _normalise(str(update.value)) in current)


def _canonical_value(key: str, value):
    """Type-check a value for its canonical slot; None means reject."""
    if key == "service_type":
        try:
            return ServiceType(str(value).strip().upper()).value
        except ValueError:
            return None
    if key in {"budget_vnd", "decided_price", "current_mileage_km"}:
        try:
            return int(float(str(value).replace(",", "").replace(" ", "")))
        except ValueError:
            return None
    if key == "customer_phone":
        # Store the customer's own format; normalize_phone is only the validity check.
        return " ".join(str(value).split()) if normalize_phone(str(value)) else None
    if key == "car_model":
        # A typed name that matches exactly one catalog vehicle becomes that vehicle's catalog name.
        try:
            found = get_services().product.find_model(str(value), limit=2)
        except Exception:
            logger.exception("Catalog lookup for car_model failed")
            found = []
        return found[0].name if len(found) == 1 else value
    return value


def _validated_updates(state: AgentState, interpretation: ConversationInterpretation,
                       rejected: list[str], dropped: list[str] | None = None) -> list[SlotUpdate]:
    """Valid updates only; keys whose value failed validation are appended to ``rejected``.
    Every other discarded proposal is named (key + reason, never the value) in ``dropped`` for the debug trace."""
    dropped = dropped if dropped is not None else []
    text = state.get("user_text", "")
    facts = state.get("known_facts") or {}
    corrects = any(task.relationship == TaskRelationship.CORRECT_PREVIOUS_INFO for task in interpretation.tasks)
    updates: list[SlotUpdate] = []
    for update in interpretation.slot_updates:
        key = update.key
        if key not in CANONICAL_SLOTS:
            logger.warning("Discarding non-canonical slot update: %s", key)
            dropped.append(f"{key}: not a canonical slot")
            continue
        if not _evidence_is_current(update, text):
            dropped.append(f"{key}: evidence/value not found in the current message")
            logger.warning("Discarding slot update with invalid current-turn evidence: %s %s", key,
                           "" if key in {"customer_phone", "customer_name", "customer_address"}
                           else repr((update.evidence, update.value)))
            continue
        if update.operation != SlotOperation.CLEAR:
            value = _canonical_value(key, update.value)
            if value is None:
                logger.warning("Discarding slot update with invalid value for %s", key)
                dropped.append(f"{key}: invalid value")
                rejected.append(key)
                continue
            update = update.model_copy(update={"value": value})
        old = facts.get(key)
        old_value = getattr(old, "value", old)
        # Identity is only overwritten by an explicit correction, never by a stray mention.
        # Completing a given name ("Khang") with the full name ("Dương Định Khang") is not an overwrite.
        completes = (key == "customer_name" and old_value and not is_full_name(str(old_value))
                     and is_full_name(str(update.value)))
        if (key == "customer_name" and old_value and old_value != update.value and not corrects
                and not completes and update.operation != SlotOperation.CORRECT):
            logger.warning("Discarding customer_name update that would overwrite a known name")
            dropped.append(f"{key}: would overwrite a known name")
            continue
        operation = update.operation
        if operation == SlotOperation.SET and old is not None and old_value != update.value:
            operation = SlotOperation.CORRECT  # conflict: the latest explicit statement wins
        updates.append(update.model_copy(update={"operation": operation}))
    return updates


def _car_list(state: AgentState) -> list[str]:
    """The cars the customer counts ('mẫu số 1', 'mẫu số 2'), as the last bot message presented them."""
    value = fact_value(state.get("known_facts"), "car_list")
    return [str(item) for item in value] if isinstance(value, list) else []


def _is_catalog_car(name: str | None) -> bool:
    """True when ``name`` is exactly the name of a vehicle in the catalog (a hallucinated name never is)."""
    if not name:
        return False
    try:
        return any(item.name == name for item in get_services().product.find_model(str(name), limit=5))
    except Exception:
        logger.exception("Catalog lookup for car reference failed")
        return False


def _resolve_references(state: AgentState, interpretation: ConversationInterpretation) -> ConversationInterpretation:
    """The model counts ("xe số 2" -> the second car it was given); the code only checks the answer is one of the
    cars the customer can count (the list the bot just presented, or the one before), so a hallucinated name never
    reaches a specialist."""
    known = {*interpretation.presented_cars, *_car_list(state)}
    # car_list can be empty although the customer already chose a car (the bot's list was never captured); a car
    # that exists in the catalog under exactly that name is just as safe to pass on as one from the list.
    tasks = [task.model_copy(update={"car_model": task.car_model if task.car_model in known
                                     or _is_catalog_car(task.car_model) else None})
             for task in interpretation.tasks]
    return interpretation.model_copy(update={"tasks": tasks})


_CAR_CHOICE_ACTIONS = {"purchase_interest", "start_contract"}


def _sync_car_model(state: AgentState, interpretation: ConversationInterpretation, facts: dict) -> None:
    """Keep car_model on the car the customer is buying. A purchase/contract/showroom task that points at a car
    ("mua mẫu số 2", "xe này") sets it; a different car than before also drops the price agreed for the
    old one. Spec questions never touch it (it is also the vehicle used for maintenance answers)."""
    showroom = fact_value(facts, "service_type") in (ServiceType.SHOWROOM_VISIT.value, ServiceType.CONTRACT_SIGN.value)
    chosen = {task.car_model for task in interpretation.tasks if task.car_model and (
        task.action in _CAR_CHOICE_ACTIONS or (task.action == "book_appointment" and showroom))}
    if len(chosen) == 1 and not any(u.key == "car_model" for u in interpretation.slot_updates):
        name = chosen.pop()
        facts["car_model"] = KnownFact(key="car_model", value=name, source=FactSource.INFERRED)
    before = fact_value(state.get("known_facts"), "car_model")
    after = fact_value(facts, "car_model")
    if before and after and before != after:
        facts.pop("decided_price", None)


def _phone_fallback(state: AgentState, applied: ConversationInterpretation) -> list[SlotUpdate]:
    """A phone number has a fixed format, so it does not depend on the model alone: when the message holds exactly one
    valid Vietnamese number, the model proposed none and no phone is known yet, it is stored. A different number
    than the known one is left to the model (a correction needs its judgement)."""
    if any(update.key == "customer_phone" for update in applied.slot_updates):
        return []
    if fact_value(state.get("known_facts"), "customer_phone"):
        return []
    found = {match.group(0) for match in _PHONE_RE.finditer(state.get("user_text", ""))}
    if len(found) != 1 or not normalize_phone(phone := found.pop()):
        return []
    return [SlotUpdate(key="customer_phone", operation=SlotOperation.SET, value=phone, evidence=phone)]


def _apply_interpretation(state: AgentState, interpretation: ConversationInterpretation) -> dict:
    """Validate the model output, then write accepted slot updates into known_facts."""
    rejected: list[str] = []
    dropped: list[str] = []
    # The cars the bot's last message listed replace the list the customer counts. The model names them;
    # the code keeps the previous list when it names none, so a chatty reply never wipes the count.
    facts = dict(state.get("known_facts") or {})
    if interpretation.presented_cars:
        facts["car_list"] = KnownFact(key="car_list", value=list(interpretation.presented_cars),
                                      source=FactSource.INFERRED)
    state = {**state, "known_facts": facts}
    applied = _resolve_references(state, interpretation)
    applied = applied.model_copy(update={"slot_updates": _validated_updates(state, applied, rejected, dropped)})
    applied = applied.model_copy(update={"slot_updates": [*applied.slot_updates, *_phone_fallback(state, applied)]})
    for update in applied.slot_updates:
        if update.operation == SlotOperation.CLEAR:
            facts.pop(update.key, None)
            continue
        source = FactSource.USER_CORRECTED if update.operation == SlotOperation.CORRECT else FactSource.USER_EXPLICIT
        facts[update.key] = KnownFact(key=update.key, value=update.value, source=source,
                                      updated_at_message_id=update.source_message_id)

    _sync_car_model(state, applied, facts)
    if any(update.key == "appointment_when" for update in applied.slot_updates):
        sync_date_fact(facts)  # the customer's words -> a calendar date, computed in code
    return {"conversation_interpretation": applied, "known_facts": facts, "rejected_slots": rejected,
            # Debug trace only (keys and reasons, no values): what the model proposed and what validation dropped.
            "proposed_slots": [update.key for update in interpretation.slot_updates], "dropped_slots": dropped}


def _trace_safe(value, key: str | None = None):
    sensitive = {"customer_phone", "phone", "customer_name", "customer_address", "evidence"}
    if key and key.casefold() in sensitive:
        return "[REDACTED]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        # A slot update names its key in "key"; redact its value by that name.
        hidden = value.get("key") if isinstance(value.get("key"), str) and value["key"] in sensitive else None
        return {str(k): "[REDACTED]" if hidden and k == "value" else _trace_safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_safe(v) for v in value]
    if isinstance(value, str) and _PHONE_RE.search(value):
        return "[REDACTED]"
    return value


def _write_preprocess_trace(result: dict) -> None:
    path = Path(__file__).resolve().parents[3] / "trace.json"
    payload = {"preprocess_result": _trace_safe(result), "fields": list(result)}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("Could not write preprocess trace at %s", path)


def preprocess(state: AgentState) -> dict:
    text = state.get("user_text", "")
    interpretation = generate_structured(_context_prompt(state), text, ConversationInterpretation,
                                         state.get("history"))
    if interpretation is None:
        interpretation = _neutral_interpretation(text, "LLM unavailable")
    result = _apply_interpretation(state, interpretation)
    result["trace"] = ["preprocess"]
    _write_preprocess_trace(result)
    return result
