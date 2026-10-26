"""Canonical conversation interpretation for one chat turn.

The structured ``ConversationInterpretation`` returned by the configured LLM is
 the only interpretation consumed by the graph.  This module deliberately does
not translate from the former intent/slot extraction format.
"""
import json
import logging
import re
import uuid
from pathlib import Path

from app.agent.llm import generate_structured
from app.agent.nodes.common import step
from app.agent.nodes.context import assemble_context
from app.agent.state import AgentState
from app.contracts.schemas import (
    ConversationInterpretation,
    FactSource,
    Intent,
    InteractionKind,
    InteractionStatus,
    KnownFact,
    SlotOperation,
    SlotUpdate,
    TaskInterpretation,
    TaskRelationship,
)
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_NO_ACTION_DOMAINS = {Intent.GREETING, Intent.OTHER}
_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)\d{9}(?!\d)")
# (pattern, need_caps): the last pattern is a whole-message self-introduction such as "Em Lan",
# too ambiguous without capitalised name words ("em đi", "em ok").
_NAME_PATTERNS = (
    (re.compile(r"(?:tên\s+(?:tôi|mình|em|anh|chị)\s+(?:là\s+)?|(?:tôi|mình|em|anh|chị)\s+tên\s+(?:là\s+)?)([^,.!?\n]+)", re.I), False),
    (re.compile(r"(?:tôi|mình|em|anh|chị)\s+là\s+([^,.!?\n]+)", re.I), False),
    (re.compile(r"^\s*(?i:em|anh|chị|mình)\s+([^\W\d_]+(?:\s+[^\W\d_]+){0,3})\s*[.!]?\s*$"), True),
)
# Words that end a captured name ("Nam sdt 09...", "Nam muốn đặt lịch").
_NAME_BOUNDARY = {
    "sdt", "sđt", "số", "và", "muốn", "cần", "ở", "để", "đến", "xin", "nhé", "nha", "ạ", "đang", "vừa",
    "cho", "hỏi", "đặt", "mua", "gọi", "liên", "hệ", "với", "thì", "nên", "vì",
}
# Words that never appear in a person's name: greetings, vehicles, scheduling, roles, places.
_NON_NAME_WORDS = {
    "xin", "chào", "alo", "hello", "hi", "vâng", "dạ", "ok", "okay", "được", "không", "có", "ạ", "nhé",
    "cảm", "ơn", "xe", "tải", "tấn", "ben", "khách", "người", "muốn", "cần", "mua", "bán", "đặt", "lịch",
    "hẹn", "bảo", "dưỡng", "sửa", "chữa", "lái", "thử", "showroom", "xưởng", "giá", "tiền", "triệu", "tỷ",
    "ngày", "tuần", "tháng", "giờ", "mai", "hôm", "nay", "sáng", "chiều", "tối", "ở", "tại", "là", "của",
    "và", "với", "đang", "sẽ", "đã", "rất", "đây", "này", "foton", "gara", "garage", "sdt", "sđt",
}
_PLACE_NAMES = {"hà nội", "sài gòn", "hồ chí minh", "đà nẵng", "hải phòng", "cần thơ"}
_NAME_WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_MAX_NAME_WORDS = 5
_MAX_NAME_LENGTH = 60

INTERPRETATION_PROMPT = """\
Bạn là bộ phân tích hội thoại của trợ lý quản lý gara. Hãy trả về đúng schema
ConversationInterpretation và chỉ phân tích tin nhắn mới nhất.

Nguyên tắc bắt buộc:
- tasks phải là các việc ứng dụng thực sự có thể thực hiện, theo thứ tự xuất hiện.
- Một yêu cầu xem xe trực tiếp/showroom hoặc đặt lịch lái thử là APPOINTMENT;
  chỉ thêm SALES khi khách cũng hỏi giá, thông số, mẫu xe hoặc danh mục.
- Không suy đoán intended_use, tải trọng, mẫu xe, ngày giờ, số điện thoại hay
  bất kỳ fact nào. Chỉ tạo slot_updates khi người dùng nói rõ trong tin nhắn mới.
- evidence phải là một đoạn nguyên văn (hoặc gần nguyên văn) của tin nhắn mới.
- Dùng ANSWER_TO_QUESTION/ACCEPT_OFFER/REJECT_OFFER khi tin nhắn trả lời một
  pending interaction đang mở; dùng CHANGE_TOPIC khi bỏ workflow cũ để yêu cầu
  việc khác; dùng CORRECT_PREVIOUS_INFO khi sửa fact đã biết.
- Chỉ dùng entity_id khi nó có trong selected_entities được cung cấp. Với "xe số
  1", "xe thứ 2" hoặc "nó", không tự chọn sản phẩm ngoài selected_entities.
- catalog_request chỉ true khi khách rõ ràng yêu cầu xem danh sách/danh mục.
- Nếu không chắc, đặt uncertain=true và tạo task general_conversation với
  requires_clarification=true thay vì đoán.
- tasks và message_summary CHỈ phân tích nội dung/yêu cầu mới trong tin nhắn mới nhất; KHÔNG lặp lại các tasks hoặc gom lại toàn bộ yêu cầu cũ đã xử lý từ các lượt trước.
- Không biến showroom/appointment thành yêu cầu tìm danh mục.
- customer_name: chỉ tạo slot_update khi khách tự xưng tên mình (ví dụ "tôi tên Nam",
  "anh là Minh Tuấn", "em Lan") hoặc trả lời đúng câu hỏi tên của bot. value chỉ là tên
  người, không kèm xưng hô hay số điện thoại. KHÔNG dùng cho tên xe/sản phẩm, địa danh,
  lời chào, con số, ngày giờ hay vai trò (khách, người mua). Nếu known_facts đã có
  customer_name, chỉ cập nhật khi khách sửa rõ ràng (CORRECT_PREVIOUS_INFO).

Các action hợp lệ và context đầy đủ được đặt trong user message bên dưới.
"""


def _context_prompt(context) -> str:
    # History and current text are supplied as actual chat messages by llm.py;
    # keep only state metadata here to avoid sending either twice.
    metadata = context.model_dump(mode="json", exclude={"history", "current_message"})
    return "\n".join((
        INTERPRETATION_PROMPT,
        "\nCONVERSATION STATE JSON:",
        json.dumps(metadata, ensure_ascii=False, default=str),
    ))


def _neutral_interpretation(text: str, reason: str) -> ConversationInterpretation:
    """Safe canonical result when structured interpretation is unavailable.

    It intentionally performs no business action and creates no facts.  A missing
    LLM must never silently reactivate the removed legacy interpreter.
    """
    task = TaskInterpretation(
        task_id=f"task_{uuid.uuid4().hex[:8]}",
        domain=Intent.OTHER,
        action="general_conversation",
        text=text,
        relationship=TaskRelationship.UNCLEAR,
        requires_clarification=True,
        clarification_question="Mình chưa hiểu rõ yêu cầu. Bạn nói rõ hơn giúp mình nhé?",
    )
    return ConversationInterpretation(
        message_summary=f"Structured interpretation unavailable: {reason}",
        tasks=[task],
        unresolved_ambiguities=[reason],
        uncertain=True,
    )


def _interpret(state: AgentState) -> ConversationInterpretation:
    context = assemble_context(state, message_id=None)
    text = context.current_message.get("text", "")
    result = generate_structured(
        _context_prompt(context), text, ConversationInterpretation, context.history
    )
    return result if result is not None else _neutral_interpretation(text, "LLM unavailable")


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split())


def _evidence_is_current(update: SlotUpdate, text: str) -> bool:
    """Reject fabricated evidence before a model-proposed fact enters state."""
    if update.operation == SlotOperation.CLEAR:
        return bool(update.evidence.strip()) and _normalise(update.evidence) in _normalise(text)
    if update.value is None or not update.evidence.strip():
        return False
    evidence = _normalise(update.evidence)
    current = _normalise(text)
    return evidence in current and (
        _normalise(str(update.value)) in current
        or update.key in {"appointment_when", "when_text", "service_type", "service_detail", "budget_vnd"}
    )


def _validated_updates(state: AgentState, interpretation: ConversationInterpretation) -> list[SlotUpdate]:
    text = state.get("user_text", "")
    corrects = any(task.relationship == TaskRelationship.CORRECT_PREVIOUS_INFO for task in interpretation.tasks)
    updates: list[SlotUpdate] = []
    for update in interpretation.slot_updates:
        if update.key == "customer_name" and update.operation != SlotOperation.CLEAR:
            name = _valid_name(update.value)
            old_name = _fact_value(state.get("known_facts", {}).get("customer_name"))
            if name is None or (old_name and old_name != name and not corrects):
                logger.warning("Discarding customer_name update: invalid or would overwrite known name")
                continue
            update = update.model_copy(update={"value": name})
        if not _evidence_is_current(update, text):
            logger.warning("Discarding slot update with invalid current-turn evidence: %s", update.key)
            continue
        old = state.get("known_facts", {}).get(update.key)
        operation = update.operation
        if operation == SlotOperation.SET and old is not None and old.value != update.value:
            operation = SlotOperation.CORRECT
        updates.append(update.model_copy(update={"operation": operation}))
    return updates


def _apply_interpretation(state: AgentState, interpretation: ConversationInterpretation) -> dict:
    updates = _validated_updates(state, interpretation)
    facts = dict(state.get("known_facts", {}))
    for update in updates:
        if update.operation == SlotOperation.CLEAR:
            facts.pop(update.key, None)
            continue
        source = (FactSource.USER_CORRECTED if update.operation == SlotOperation.CORRECT
                  else FactSource.USER_EXPLICIT)
        facts[update.key] = KnownFact(
            key=update.key,
            value=update.value,
            source=source,
            updated_at_message_id=update.source_message_id,
        )
    applied = interpretation.model_copy(update={"slot_updates": updates})
    return {
        "conversation_interpretation": applied,
        "known_facts": facts,
        "complexity": applied.complexity,
    }


def _valid_name(value) -> str | None:
    """Return a cleaned person name, or None when the value cannot plausibly be one.

    Shared by the LLM slot update and the regex fallback so both reject numbers,
    phone numbers, greetings, vehicle/scheduling words and places.
    """
    if not isinstance(value, str):
        return None
    name = " ".join(value.split()).strip(" .,;:!?-")
    if not name or len(name) > _MAX_NAME_LENGTH or any(ch.isdigit() for ch in name):
        return None
    words = name.split()
    if len(words) > _MAX_NAME_WORDS or not all(_NAME_WORD_RE.fullmatch(word) for word in words):
        return None
    if name.casefold() in _PLACE_NAMES or any(word.casefold() in _NON_NAME_WORDS for word in words):
        return None
    return name


def _extract_name(text: str) -> str | None:
    for pattern, need_caps in _NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        words = []
        for word in match.group(1).split():
            if word.casefold() in _NAME_BOUNDARY or any(ch.isdigit() for ch in word):
                break
            words.append(word)
        if need_caps and not all(word[0].isupper() for word in words):
            continue
        if name := _valid_name(" ".join(words)):
            return name
    return None


def _fact_value(fact: KnownFact | None):
    return fact.value if isinstance(fact, KnownFact) else None


def _save_profile_to_db(session_id: str, name: str | None, phone: str | None) -> None:
    if not name and not phone:
        return
    db = SessionLocal()
    try:
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            session = ChatSession(id=session_id)
            db.add(session)
            db.flush()
        customer = db.query(Customer).filter(Customer.id == session.customer_id).first() if session.customer_id else None
        if not customer:
            customer = Customer()
            db.add(customer)
            db.flush()
            session.customer_id = customer.id
        if name and not customer.full_name:
            customer.full_name = name
        if phone and not customer.phone_number:
            customer.phone_number = phone
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not save customer profile")
    finally:
        db.close()


def _trace_safe(value, key: str | None = None):
    sensitive = {"customer_phone", "phone", "customer_name", "customer_address", "evidence"}
    if key and key.casefold() in sensitive:
        return "[REDACTED]"
    if isinstance(value, KnownFact):
        value = value.model_dump(mode="json")
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _trace_safe(v, str(k)) for k, v in value.items()}
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
    interpretation = _interpret(state)
    result = _apply_interpretation(state, interpretation)
    facts = result["known_facts"]
    name = _fact_value(facts.get("customer_name"))
    if not name and (name := _extract_name(state.get("user_text", ""))):
        facts["customer_name"] = KnownFact(key="customer_name", value=name, source=FactSource.USER_EXPLICIT)
    phone = _fact_value(facts.get("customer_phone"))
    if state.get("session_id") and (name or phone):
        _save_profile_to_db(state["session_id"], name, phone)
    result["trace"] = step(state, "preprocess")
    _write_preprocess_trace(result)
    return result


# Kept as a narrow inspection helper for callers that only need the canonical
# validation step; it accepts no legacy extraction model.
def validate_interpretation(state: AgentState, interpretation: ConversationInterpretation) -> ConversationInterpretation:
    return interpretation.model_copy(update={"slot_updates": _validated_updates(state, interpretation)})
