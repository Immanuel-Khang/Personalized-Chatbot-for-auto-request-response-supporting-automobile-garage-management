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
_NAME_PATTERNS = (
    re.compile(r"(?:tên\s+(?:tôi|mình|em)\s+(?:là\s+)?|(?:tôi|mình|em)\s+tên\s+)([^,.!?]+)", re.I),
    re.compile(r"(?:tôi|mình|em)\s+là\s+([^,.!?]+)", re.I),
)

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
    updates: list[SlotUpdate] = []
    for update in interpretation.slot_updates:
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


def _extract_name(text: str) -> str | None:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group(1).strip()
            return name if name else None
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
    name = _extract_name(state.get("user_text", ""))
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
