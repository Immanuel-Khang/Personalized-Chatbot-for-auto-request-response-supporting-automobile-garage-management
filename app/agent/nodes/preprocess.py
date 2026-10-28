"""Preprocessing Agent: one structured LLM interpretation of the current message.

It performs the six preprocessing jobs (contextual intent, task identification,
slot extraction, reference resolution, ambiguity/conflict detection, structured
output) and then *validates* the model output in application code:
- slot keys must be canonical and backed by current-turn evidence;
- a SET over an existing different value becomes a CORRECT (latest wins);
- product references resolve only against products the bot actually showed.
Downstream nodes read this result; none of them re-parse the Vietnamese text.
Preprocessing has no side effects other than writing the debug trace.
"""
import json
import logging
import re
import uuid
from pathlib import Path

from app.agent.llm import generate_structured
from app.agent.nodes.input import build_context
from app.agent.state import AgentState
from app.identity import normalize_phone
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
    "service_type": ("Loại lịch hẹn: MAINTENANCE (bảo dưỡng) | REPAIR (sửa chữa) | TEST_DRIVE (lái thử) | "
                     "SHOWROOM_VISIT (xem xe tại showroom)"),
    "service_detail": "Hạng mục/bộ phận cần xử lý (kính, phanh, điều hòa...)",
    "appointment_when": "Ngày giờ hẹn đúng như khách nói (VD 'sáng thứ 7 tuần sau')",
    "customer_phone": "Số điện thoại khách",
    "customer_name": ("Tên khách tự xưng, viết chuẩn có dấu và hoa chữ cái đầu (dương định khang -> Dương Định Khang); "
                      "chỉ phần tên khách nói, có thể chỉ tên gọi; không kèm xưng hô anh/chị/em"),
    "customer_address": "Địa chỉ khách",
    "decided_price": "Giá chốt hợp đồng (VND)",
    "current_mileage_km": "Số km hiện tại của xe (số)",
}
_ALIASES = {"phone": "customer_phone", "when_text": "appointment_when", "budget": "budget_vnd", "name": "customer_name"}
# Values that are usually paraphrased/normalised by the model (e.g. "500 triệu" -> 500000000).
_NORMALISED_VALUE_KEYS = {"appointment_when", "service_type", "service_detail", "budget_vnd", "customer_name",
                          "decided_price", "payload_tons", "current_mileage_km", "intended_use"}

INTERPRETATION_PROMPT = """\
Bạn là Preprocessing Agent của trợ lý gara ô tô. Phân tích TIN NHẮN MỚI NHẤT của khách
trong ngữ cảnh lịch sử hội thoại và CONVERSATION STATE, trả về đúng schema
ConversationInterpretation. Các node sau chỉ đọc kết quả của bạn, không đọc lại câu chữ.

1. Ý định theo ngữ cảnh (relationship của từng task):
   - Tin nhắn trả lời câu bot vừa hỏi (VD bot hỏi "cần xe để làm gì?", khách đáp "Chở hàng")
     là ANSWER_TO_QUESTION của việc đang làm, KHÔNG phải câu hỏi mới. Đặt referenced_interaction_id nếu khớp một pending_interactions. Task trả lời mang domain/action
     của việc đang làm (VD đang tư vấn xe -> SALES search_catalog), KHÔNG dùng OTHER.
   - Đồng ý/từ chối gợi ý của bot: ACCEPT_OFFER / REJECT_OFFER (accepts_offer=true/false).
   - Sửa thông tin đã nói ("Không, 700 triệu"): CORRECT_PREVIOUS_INFO và slot_update CORRECT.
   - Nói tiếp workflow đang mở: CONTINUE_TASK. Bỏ việc cũ để làm việc khác: CHANGE_TOPIC.
   - Hủy/không muốn tiếp tục việc đang làm: CANCEL_TASK.
2. Tách task: mỗi yêu cầu riêng biệt là một task, đúng thứ tự xuất hiện. "Tìm xe số 1 và đặt
   lịch xem xe" = task SALES product_detail + task APPOINTMENT book_appointment. Chỉ phân tích
   yêu cầu MỚI; không lặp lại task của các lượt trước. Xem xe tại showroom/lái thử là APPOINTMENT.
   Mặc cả, xin giảm giá, xin quà là DISCOUNT. Khách chỉ HỎI xưởng có làm dịch vụ X không, hoặc hỏi
   thủ tục/giấy tờ, mà chưa yêu cầu đặt lịch/làm hợp đồng: inquiry_only=true.
3. Slot: chỉ dùng key trong canonical_slots. Chỉ tạo slot_update khi khách nói rõ trong tin nhắn
   mới (hoặc tin nhắn là câu trả lời ngắn cho câu bot vừa hỏi về đúng slot đó). evidence là đoạn
   nguyên văn trong tin nhắn mới. Không suy đoán giá trị. Khi khách đặt lịch, ghi service_type theo
   loại lịch khách nói: "xem xe" -> SHOWROOM_VISIT, "lái thử" -> TEST_DRIVE, "bảo dưỡng" -> MAINTENANCE,
   "sửa" -> REPAIR (evidence là cụm từ đó). Khách nêu thời gian hẹn dù chưa có giờ cụ thể ("chiều thứ 2
   tuần sau", "sáng mai") -> vẫn ghi appointment_when đúng như khách nói; nhân viên sẽ chốt giờ, đó KHÔNG
   phải điểm mơ hồ. Khách sửa tên/SĐT/địa chỉ -> slot_update CORRECT cho đúng key đó.
   Tên khách (customer_name): chỉ khi khách tự giới thiệu hoặc trả lời câu hỏi tên. Ghi dạng chuẩn có dấu, viết hoa
   chữ cái đầu; khách chỉ nói tên gọi ("Khang", "anh Khang") thì ghi đúng phần đó, KHÔNG tự thêm họ. Xưng hô,
   lời chào, tên xe, địa danh không phải tên. evidence là cụm khách gõ nguyên văn.
4. Tham chiếu: "xe số 1", "mẫu thứ ba", "nó", "xe đó" -> thêm vào references với position theo
   shown_products (1-based, -1 = cuối) và entity_id lấy từ shown_products; gán entity_id cho task
   liên quan. Không có shown_products phù hợp hoặc nhiều khả năng -> ambiguous=true, không đoán.
5. Mơ hồ: thiếu thông tin mà workflow sẽ tự hỏi (SĐT, ngày giờ) không phải là mơ hồ. Không hiểu tin nhắn
   hoặc có điểm mơ hồ chặn task hiện tại: uncertain=true và một task general_conversation requires_clarification=true.
6. signals: wants_human (khách muốn gặp nhân viên/người thật), complaint (phàn nàn, bức xúc),
   safety_risk (xe có dấu hiệu nguy hiểm: mất phanh, cháy, khói, mất lái...), discount_request,
   deal_change (khách muốn sửa điều khoản hợp đồng, thương lượng lại giá, hoặc hỏi phí/chi phí phát sinh
   của hợp đồng; KHÔNG bật khi khách chỉ trả lời giá chốt bot hỏi hay khi chữ "giá/phí" nằm trong tên riêng).
   complexity=HIGH chỉ khi câu hỏi kỹ thuật vượt quá tư vấn thông thường (chẩn đoán phức tạp).
"""


def _context_prompt(state: AgentState) -> str:
    # History and current text go to the LLM as chat messages (llm.py); send only state here.
    context = build_context(state, CANONICAL_SLOTS)
    metadata = context.model_dump(mode="json", exclude={"history", "current_message"})
    return f"{INTERPRETATION_PROMPT}\nCONVERSATION STATE JSON:\n{json.dumps(metadata, ensure_ascii=False, default=str)}"


def _neutral_interpretation(text: str, reason: str) -> ConversationInterpretation:
    """Safe result when the LLM is unavailable: no business action, no facts."""
    task = TaskInterpretation(
        task_id=f"task_{uuid.uuid4().hex[:8]}", domain=Intent.OTHER, action="general_conversation",
        text=text, relationship=TaskRelationship.UNCLEAR, requires_clarification=True)
    return ConversationInterpretation(message_summary=f"Structured interpretation unavailable: {reason}",
                                      tasks=[task], uncertain=True)


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


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
    return value


def _validated_updates(state: AgentState, interpretation: ConversationInterpretation,
                       rejected: list[str]) -> list[SlotUpdate]:
    """Valid updates only; keys whose value failed validation are appended to ``rejected``."""
    text = state.get("user_text", "")
    facts = state.get("known_facts") or {}
    corrects = any(task.relationship == TaskRelationship.CORRECT_PREVIOUS_INFO for task in interpretation.tasks)
    updates: list[SlotUpdate] = []
    for update in interpretation.slot_updates:
        key = _ALIASES.get(update.key, update.key)
        if key not in CANONICAL_SLOTS:
            logger.warning("Discarding non-canonical slot update: %s", update.key)
            continue
        update = update.model_copy(update={"key": key})
        if not _evidence_is_current(update, text):
            logger.warning("Discarding slot update with invalid current-turn evidence: %s %s", key,
                           "" if key in {"customer_phone", "customer_name", "customer_address"}
                           else repr((update.evidence, update.value)))
            continue
        if update.operation != SlotOperation.CLEAR:
            value = _canonical_value(key, update.value)
            if value is None:
                logger.warning("Discarding slot update with invalid value for %s", key)
                rejected.append(key)
                continue
            update = update.model_copy(update={"value": value})
        old = facts.get(key)
        old_value = getattr(old, "value", old)
        # Identity is only overwritten by an explicit correction, never by a stray mention.
        if (key == "customer_name" and old_value and old_value != update.value and not corrects
                and update.operation != SlotOperation.CORRECT):
            logger.warning("Discarding customer_name update that would overwrite a known name")
            continue
        operation = update.operation
        if operation == SlotOperation.SET and old is not None and old_value != update.value:
            operation = SlotOperation.CORRECT  # conflict: the latest explicit statement wins
        updates.append(update.model_copy(update={"operation": operation}))
    return updates


def _resolve_references(state: AgentState, interpretation: ConversationInterpretation) -> ConversationInterpretation:
    """Ground references and task entity ids in the products the bot actually showed."""
    shown = [item for item in state.get("selected_entities") or [] if item.get("entity_type") == "PRODUCT"]
    ids = {item["entity_id"] for item in shown}
    references = []
    for ref in interpretation.references:
        entity_id, ambiguous = ref.entity_id, ref.ambiguous
        if ref.position is not None:
            index = len(shown) - 1 if ref.position == -1 else ref.position - 1
            positional = shown[index]["entity_id"] if 0 <= index < len(shown) else None
            if positional is None or (entity_id is not None and entity_id != positional):
                entity_id, ambiguous = None, True
            else:
                entity_id = positional
        elif entity_id not in ids:
            entity_id, ambiguous = None, True
        references.append(ref.model_copy(update={"entity_id": entity_id, "ambiguous": ambiguous}))

    resolved = [ref.entity_id for ref in references if ref.entity_id is not None]
    tasks = []
    for task in interpretation.tasks:
        entity_id = task.entity_id if task.entity_id in ids else None
        if entity_id is None and len(resolved) == 1 and task.domain in (Intent.SALES, Intent.APPOINTMENT,
                                                                         Intent.CONTRACT, Intent.DISCOUNT):
            entity_id = resolved[0]
        tasks.append(task.model_copy(update={"entity_id": entity_id,
                                             "entity_type": "PRODUCT" if entity_id is not None else None}))
    return interpretation.model_copy(update={"references": references, "tasks": tasks})


def _apply_interpretation(state: AgentState, interpretation: ConversationInterpretation) -> dict:
    """Validate the model output, then write accepted slot updates into known_facts."""
    rejected: list[str] = []
    applied = _resolve_references(state, interpretation)
    applied = applied.model_copy(update={"slot_updates": _validated_updates(state, applied, rejected)})
    facts = dict(state.get("known_facts") or {})
    for update in applied.slot_updates:
        if update.operation == SlotOperation.CLEAR:
            facts.pop(update.key, None)
            continue
        source = FactSource.USER_CORRECTED if update.operation == SlotOperation.CORRECT else FactSource.USER_EXPLICIT
        facts[update.key] = KnownFact(key=update.key, value=update.value, source=source,
                                      updated_at_message_id=update.source_message_id)
    return {"conversation_interpretation": applied, "known_facts": facts, "rejected_slots": rejected}


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
