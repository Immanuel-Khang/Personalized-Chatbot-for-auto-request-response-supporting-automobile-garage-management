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
import unicodedata
import uuid
from pathlib import Path

from app.agent.llm import generate_structured
from app.agent.nodes.input import AVAILABLE_ACTIONS
from app.agent.state import AgentState, fact_value, open_pending
from app.identity import normalize_phone, is_full_name
from app.contracts.schemas import (
    ConversationInterpretation,
    FactSource,
    Intent,
    InteractionKind,
    KnownFact,
    PendingInteraction,
    ServiceType,
    ShortReplyInterpretation,
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
                     "SHOWROOM_VISIT (xem xe tại showroom). Xưởng KHÔNG có dịch vụ lái thử"),
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
trong ngữ cảnh hội thoại (tóm tắt các lượt trước + các tin gần nhất) và CONVERSATION STATE, trả về đúng schema
ConversationInterpretation. Các node sau chỉ đọc kết quả của bạn, không đọc lại câu chữ.

1. Ý định theo ngữ cảnh (relationship của từng task):
   - Tin nhắn trả lời câu bot vừa hỏi (VD bot hỏi "cần xe để làm gì?", khách đáp "Chở hàng")
     là ANSWER_TO_QUESTION của việc đang làm, KHÔNG phải câu hỏi mới. Đặt referenced_interaction_id nếu khớp một pending_interactions. Task trả lời mang domain/action
     của việc đang làm (VD đang tư vấn xe -> SALES search_catalog), KHÔNG dùng OTHER.
   - Đồng ý/từ chối gợi ý của bot: ACCEPT_OFFER / REJECT_OFFER (accepts_offer=true/false).
   - Sửa thông tin đã nói ("Không, 700 triệu"): CORRECT_PREVIOUS_INFO và slot_update CORRECT.
   - Nói tiếp workflow đang mở: CONTINUE_TASK. Bỏ việc cũ để làm việc khác: CHANGE_TOPIC.
   - Hủy/không muốn tiếp tục việc đang làm: CANCEL_TASK.
   - Lịch hẹn ĐÃ ĐẶT: "hủy lịch hẹn của tôi" -> APPOINTMENT cancel_appointment (NEW_REQUEST, không phải
     CANCEL_TASK); "đổi lịch sang ngày mai" -> APPOINTMENT reschedule_appointment, kèm slot_update appointment_when;
     "lịch hẹn của tôi lúc mấy giờ", "đã xác nhận chưa" -> APPOINTMENT appointment_details (inquiry_only=false).
     Khách đồng ý câu bot hỏi xác nhận đổi/hủy -> ACCEPT_OFFER (accepts_offer=true), KHÔNG phải CANCEL_TASK.
2. Tách task: mỗi yêu cầu riêng biệt là một task, đúng thứ tự xuất hiện. "Tìm xe số 1 và đặt
   lịch xem xe" = task SALES product_detail + task APPOINTMENT book_appointment. Chỉ phân tích
   yêu cầu MỚI; không lặp lại task của các lượt trước. Xem xe tại showroom là APPOINTMENT.
   Hỏi thông số/giá của một mẫu xe trong catalog ("động cơ của FUSO Canter FI170 là gì", "xe số 2 hộp số mấy
   cấp") là SALES product_specs, KHÔNG phải KNOWLEDGE; KNOWLEDGE chỉ cho câu hỏi kỹ thuật chung, sửa chữa, an toàn.
   Khách chỉ nói muốn mua một xe/mẫu ("tôi muốn mua mẫu 2", "lấy xe này") mà chưa nói làm hợp đồng, báo giá
   hay đặt lịch: SALES purchase_intent (gán entity_id nếu tham chiếu rõ), KHÔNG phải product_detail. Khách nêu
   nhu cầu/tiêu chí (tải trọng, ngân sách, mục đích) để tìm xe thì là search_catalog dù có chữ "mua".
   Mặc cả, xin giảm giá, xin quà là DISCOUNT. Khách chỉ HỎI xưởng có làm dịch vụ X không, hoặc hỏi
   thủ tục/giấy tờ, mà chưa yêu cầu đặt lịch/làm hợp đồng: inquiry_only=true.
3. Slot: chỉ dùng key trong canonical_slots. Chỉ tạo slot_update khi khách nói rõ trong tin nhắn
   mới (hoặc tin nhắn là câu trả lời ngắn cho câu bot vừa hỏi về đúng slot đó). evidence là đoạn
   nguyên văn trong tin nhắn mới. Không suy đoán giá trị. Tin nhắn có task APPOINTMENT mà nêu loại lịch thì
   BẮT BUỘC có slot_update service_type, kể cả khi cùng câu còn có ngày giờ: "xem xe", "xem xe trực tiếp",
   "đến showroom", "qua xem xe" -> SHOWROOM_VISIT; "bảo dưỡng" -> MAINTENANCE; "sửa" -> REPAIR
   (evidence là cụm từ đó). Xưởng không có dịch vụ lái thử: KHÔNG ghi service_type cho "lái thử". Khách nêu thời gian hẹn dù chưa có giờ cụ thể ("chiều thứ 2
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
   complexity=HIGH chỉ khi câu hỏi kỹ thuật vượt quá tư vấn thông thường (chẩn đoán phức tạp). Tin nhắn có
   nhiều yêu cầu thông thường (tìm xe + đặt lịch...), lời chào, hỏi giá/thông số -> LOW.
7. Xưng hô (customer_pronoun): Phát hiện cách khách tự xưng trong tin nhắn MỚI NHẤT. Chỉ ghi khi khách
   THỰC SỰ dùng đại từ nhân xưng để chỉ bản thân trong câu có hành động/yêu cầu:
   - "anh muốn mua xe" / "cho anh đặt lịch" -> customer_pronoun="anh"
   - "chị cần tư vấn" / "chị hỏi giá" -> customer_pronoun="chị"
   - "em muốn xem" / "tôi muốn" / "bạn cho mình hỏi" -> customer_pronoun=null (giữ neutral)
   KHÔNG ghi khi: khách chỉ chào hỏi chung ("Chào anh"), lời xã giao, hoặc không có đại từ rõ ràng.
   Chỉ nhận "anh" hoặc "chị"; các xưng hô khác (em/bạn/tôi/mình) -> null (dùng xưng hô neutral).
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
    workflow = state.get("active_workflow")
    dynamic = {
        "pending_interactions": [item.model_dump(mode="json", exclude_none=True)
                                 for item in state.get("pending_interactions") or []],
        "active_workflow": workflow.model_dump(mode="json", exclude_none=True) if workflow else None,
        "shown_products": state.get("selected_entities") or [],
        "known_facts": {key: getattr(fact, "value", fact) for key, fact in (state.get("known_facts") or {}).items()},
    }
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
        # Completing a given name ("Khang") with the full name ("Dương Định Khang") is not an overwrite.
        completes = (key == "customer_name" and old_value and not is_full_name(str(old_value))
                     and is_full_name(str(update.value)))
        if (key == "customer_name" and old_value and old_value != update.value and not corrects
                and not completes and update.operation != SlotOperation.CORRECT):
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
        # The model sometimes prefixes the domain ("SALES purchase_intent"); consumers match the bare action.
        tasks.append(task.model_copy(update={"action": task.action.split()[-1] if task.action.strip() else task.action,
                                             "entity_id": entity_id,
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

    result = {"conversation_interpretation": applied, "known_facts": facts, "rejected_slots": rejected}
    # Only an anh/chị the customer actually typed changes address terms; otherwise the stored pair stays.
    detected = applied.customer_pronoun
    if detected in ("anh", "chị") and re.search(rf"(?<!\w){detected}(?!\w)", state.get("user_text", ""), re.IGNORECASE):
        result["customer_pronoun"], result["bot_pronoun"] = detected, "em"
    elif detected in ("anh", "chị"):
        logger.warning("Discarding customer_pronoun %r not present in the message", detected)
    return result


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


def _write_preprocess_trace(result: dict, tier: int) -> None:
    path = Path(__file__).resolve().parents[3] / "trace.json"
    payload = {"tier": tier, "preprocess_result": _trace_safe(result), "fields": list(result)}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("Could not write preprocess trace at %s", path)


# ---- Tiers: the cheapest reading that is certain wins; every tier's output goes through _apply_interpretation.
# Tier 0, rules, no LLM: a pure greeting, or a bare yes/no/phone number answering the latest open interaction.
# Tier 1, small structured call: a short reply to the open question/offer of a writer workflow.
# Tier 2, the full interpretation call above: everything else, and whenever tier 0/1 is not sure.

# Workflow of a pending interaction -> the task that continues it (the router sends it to that writer).
WORKFLOW_TASK = {"appointment": (Intent.APPOINTMENT, "book_appointment"),
                 "contract": (Intent.CONTRACT, "start_contract")}
WORKFLOW_SLOTS = {
    "appointment": ("service_type", "service_detail", "appointment_when", "customer_name", "customer_phone"),
    "contract": ("customer_name", "customer_phone", "customer_address", "car_model", "decided_price"),
}
FOLLOW_UPS = {TaskRelationship.ANSWER_TO_QUESTION, TaskRelationship.ACCEPT_OFFER, TaskRelationship.REJECT_OFFER,
              TaskRelationship.CORRECT_PREVIOUS_INFO, TaskRelationship.CONTINUE_TASK}
MAX_SHORT_WORDS = 12

_TAIL = r"(?:\s+(?:ạ|nhé|nha|nhá|luôn|rồi|bạn|shop|em|anh|chị|ad))*"
_GREETING = re.compile(rf"(?:(?:xin\s+)?chào|hello|hi|alo|hey){_TAIL}")
_YES = re.compile(rf"(?:(?:dạ|vâng)\s+)?(?:ok|oke|okay|đúng|được|đồng\s+ý|vâng|dạ|ừ|ừm|uh|chốt|xác\s+nhận|chuẩn){_TAIL}")
_NO = re.compile(rf"(?:dạ\s+)?(?:không|ko|thôi|không\s+cần|thôi\s+khỏi|khỏi){_TAIL}")
_BARE_PHONE = re.compile(r"\+?\d[\d\s.\-]{7,14}")

SHORT_REPLY_PROMPT = """\
Bạn phân tích một TIN NHẮN NGẮN của khách gara ô tô, gửi ngay sau câu bot hỏi bên dưới. Trả về đúng schema
ShortReplyInterpretation.
- relationship: ANSWER_TO_QUESTION (trả lời câu bot hỏi), ACCEPT_OFFER / REJECT_OFFER (đồng ý / từ chối điều bot
  đề nghị hoặc xin xác nhận), CORRECT_PREVIOUS_INFO (sửa thông tin đã nói), CONTINUE_TASK (nói tiếp việc đang làm).
- slot_updates: chỉ dùng key trong danh sách slot; chỉ khi khách nói rõ trong tin nhắn; evidence là cụm nguyên văn
  khách gõ; không suy đoán. Sửa giá trị đã biết -> operation CORRECT. Thời gian hẹn ghi đúng như khách nói
  ("chiều thứ 7"). Tên khách viết chuẩn có dấu, hoa chữ cái đầu, không kèm anh/chị. service_type chỉ nhận
  MAINTENANCE | REPAIR | SHOWROOM_VISIT. Tiền là số nguyên VND (500 triệu -> 500000000).
- needs_full_analysis=true khi tin nhắn KHÔNG chỉ là câu trả lời cho câu bot hỏi: có yêu cầu hay câu hỏi mới, hỏi về
  xe, đổi chủ đề, hủy việc, muốn gặp nhân viên, phàn nàn, xe có dấu hiệu nguy hiểm, mặc cả giá, hoặc bạn không chắc.
"""


def _plain(text: str) -> str:
    """Casefolded words only, so "Đúng rồi ạ!" and "đúng rồi ạ" compare equal."""
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _latest_open(state: AgentState) -> PendingInteraction | None:
    items = open_pending(state)
    return items[-1] if items else None


def _workflow(pending: PendingInteraction | None) -> str:
    return str(pending.metadata.get("workflow") or "") if pending else ""


def _follow_up(text: str, summary: str, relationship: TaskRelationship, pending: PendingInteraction | None,
               greeting: bool = False, **fields) -> ConversationInterpretation:
    """One-task interpretation; it continues the pending item's workflow, or is a greeting."""
    domain, action = ((Intent.GREETING, "general_conversation") if greeting
                      else WORKFLOW_TASK.get(_workflow(pending), (Intent.OTHER, "general_conversation")))
    task = TaskInterpretation(task_id=f"task_{uuid.uuid4().hex[:8]}", domain=domain, action=action, text=text,
                              relationship=relationship,
                              referenced_interaction_id=pending.interaction_id if pending else None)
    return ConversationInterpretation(message_summary=summary, tasks=[task], **fields)


def _rule_interpretation(state: AgentState) -> ConversationInterpretation | None:
    """Tier 0: messages whose meaning is fixed by their form and the open interaction."""
    text = state.get("user_text", "").strip()
    plain = _plain(text)
    if _GREETING.fullmatch(plain):
        return _follow_up(text, "Khách chào hỏi.", TaskRelationship.NEW_REQUEST, None, greeting=True)
    pending = _latest_open(state)
    if pending is None:
        return None
    accepted = True if _YES.fullmatch(plain) else False if _NO.fullmatch(plain) else None
    if accepted is not None and pending.kind in (InteractionKind.CONFIRMATION, InteractionKind.OFFER):
        relationship = TaskRelationship.ACCEPT_OFFER if accepted else TaskRelationship.REJECT_OFFER
        return _follow_up(text, f"Khách {'đồng ý' if accepted else 'từ chối'}: {pending.text[:80]}",
                          relationship, pending, accepts_offer=accepted)
    if (_BARE_PHONE.fullmatch(text) and pending.kind == InteractionKind.QUESTION
            and "customer_phone" in WORKFLOW_SLOTS.get(_workflow(pending), ())):
        update = SlotUpdate(key="customer_phone", operation=SlotOperation.SET, value=text, evidence=text)
        return _follow_up(text, "Khách gửi số điện thoại.", TaskRelationship.ANSWER_TO_QUESTION, pending,
                          slot_updates=[update])
    return None


def _short_reply_interpretation(state: AgentState) -> ConversationInterpretation | None:
    """Tier 1: a short reply to the open interaction of a writer, read with only that question and its slots."""
    text = state.get("user_text", "").strip()
    pending = _latest_open(state)
    keys = WORKFLOW_SLOTS.get(_workflow(pending), ())
    if pending is None or not keys or "?" in text or len(text.split()) > MAX_SHORT_WORDS:
        return None
    facts = state.get("known_facts") or {}
    known = {key: fact_value(facts, key) for key in keys if fact_value(facts, key) is not None}
    system = (f"{SHORT_REPLY_PROMPT}\nCâu bot vừa hỏi ({pending.kind.value}): {pending.text}\n"
              "Slot (key: ý nghĩa):\n" + "\n".join(f"- {key}: {CANONICAL_SLOTS[key]}" for key in keys)
              + f"\nĐã biết: {json.dumps(known, ensure_ascii=False, default=str)}")
    out = generate_structured(system, text, ShortReplyInterpretation)
    if out is None or out.needs_full_analysis or out.relationship not in FOLLOW_UPS:
        return None
    accepted = {TaskRelationship.ACCEPT_OFFER: True, TaskRelationship.REJECT_OFFER: False}.get(out.relationship)
    return _follow_up(text, f"Khách trả lời câu hỏi đang chờ: {text}", out.relationship, pending,
                      slot_updates=[u for u in out.slot_updates if u.key in keys], accepts_offer=accepted)


def _interpret(state: AgentState) -> tuple[int, ConversationInterpretation]:
    """(tier, interpretation). A validation retry always gets the full call."""
    if not state.get("validation_feedback"):
        if (interpretation := _rule_interpretation(state)) is not None:
            return 0, interpretation
        if (interpretation := _short_reply_interpretation(state)) is not None:
            return 1, interpretation
    text = state.get("user_text", "")
    interpretation = generate_structured(_context_prompt(state), text, ConversationInterpretation,
                                         state.get("history"))
    return 2, interpretation or _neutral_interpretation(text, "LLM unavailable")


def preprocess(state: AgentState) -> dict:
    tier, interpretation = _interpret(state)
    logger.info("Preprocess tier %d", tier)
    result = _apply_interpretation(state, interpretation)
    result["trace"] = ["preprocess"]
    _write_preprocess_trace(result, tier)
    return result
