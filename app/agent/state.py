import operator
import re
from typing import Annotated, TypedDict

from app.contracts.schemas import (
    ActiveWorkflow,
    ConversationInterpretation,
    Intent,
    InteractionStatus,
    KnownFact,
    PendingInteraction,
)


class AgentState(TypedDict, total=False):
    session_id: str
    customer_id: int | None
    # customers row of the session ({name, phone}), loaded by handle_message before the turn; None if not linked.
    customer_profile: dict | None
    user_text: str
    mode: str
    # Previous user/assistant messages only; the current message is in user_text. The input node bounds it to
    # [{"role": "summary"} of older turns, *recent window] (agent/memory.py) before any LLM call.
    history: list[dict]
    conversation_summary: str | None
    summarized_messages: int  # leading DB history messages already folded into conversation_summary
    turn: int  # full history length before this turn; identifies the turn independent of the window
    negotiation_active: bool
    safety: bool
    route: str
    route_reason: str | None
    plan: list[dict]
    planned_agents: list[str]
    task_text: str
    task_order: int
    task_ids: list[str]
    # Second writer requested in the same turn; validation turns it into an open offer.
    deferred_workflow: dict | None
    # Result Validation -> Preprocessing loop (at most once per turn).
    validation_feedback: str | None
    validation_attempts: int
    answers: Annotated[list[dict], operator.add]
    allowed_amounts: Annotated[list[str], operator.add]
    draft_reply: str
    needs_human: bool
    handover_reason: str | None
    set_mode: str | None
    final_reply: str
    trace: Annotated[list[str], operator.add]

    # Canonical conversation interpretation and persisted conversational state.
    conversation_interpretation: ConversationInterpretation | None
    pending_interactions: list[PendingInteraction]
    known_facts: dict[str, KnownFact]
    # Current turn only: canonical slots the customer gave but that failed validation (e.g. a malformed phone).
    rejected_slots: list[str]
    active_workflow: ActiveWorkflow | None
    selected_entities: list[dict]
    # The one product the customer is currently talking about ({entity_id, name}); set by sales, used by booking.
    focused_product: dict | None


# "Chào Khang," / "Xin chào bạn!" ở đầu câu trả lời
GREETING_PREFIX = re.compile(r"^\s*(?:xin\s+)?chào(?:\s+[^\s,.!\n]+){0,3}\s*[,.!]+\s*", re.IGNORECASE)
MARKDOWN = re.compile(r"[*`]|^\s*#+\s", re.MULTILINE)
PRICE_AS_OF = re.compile(r"price_as_of\s*[:=]?\s*", re.IGNORECASE)


def strip_greeting(text: str) -> str:
    """Bỏ lời chào ở đầu câu trả lời (dùng khi hội thoại đã bắt đầu)."""
    stripped = GREETING_PREFIX.sub("", text, count=1)
    if not stripped.strip() or stripped == text:
        return text
    return stripped[0].upper() + stripped[1:]


def plain_text(text: str) -> str:
    """Markdown -> plain text: bullets become "- ", emphasis and headings are dropped."""
    text = re.sub(r"^(\s*)[*•]\s+", r"\1- ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    text = PRICE_AS_OF.sub("giá áp dụng từ ngày ", text)  # internal field name the LLM sometimes copies
    return re.sub(r"[*`]", "", text)


def clean_reply(state: AgentState, text: str) -> str:
    """A specialist's answer is the finished reply: plain text, no greeting unless the customer greeted."""
    interpretation = state.get("conversation_interpretation")
    greeted = bool(interpretation and any(task.domain == Intent.GREETING for task in interpretation.tasks))
    text = plain_text(text.strip())
    return text if greeted else strip_greeting(text)


def answer(state: AgentState, agent: str, text: str = "", needs_human: bool = False,
           handover_reason: str | None = None, executed: bool = False) -> dict:
    """Phần trả lời của một specialist, đã làm sạch (clean_reply): một task thì gửi thẳng cho khách,
    nhiều task thì compose ghép theo task_order.
    executed=True chỉ khi service xác nhận hành động (đặt lịch/hợp đồng) thành công trong lượt này."""
    return {"agent": agent, "order": state.get("task_order", 0), "text": clean_reply(state, text) if text else text,
            "needs_human": needs_human, "handover_reason": handover_reason, "executed": executed}


# Load Context (nodes/input.py) turns checkpointed facts/pending/workflow into models, so nodes read attributes.
def fact_value(facts: dict | None, key: str):
    return getattr((facts or {}).get(key), "value", None)


# The bot always says "mình"/"shop" and calls the customer "bạn", whatever the customer calls themselves.
PRONOUN_RULE = ("Xưng 'mình' (hoặc 'shop'), gọi khách là 'bạn' trong MỌI trường hợp, kể cả khi khách tự xưng "
                "anh/chị/em/tôi. KHÔNG dùng anh, chị, em, anh/chị, quý khách để gọi khách hay xưng bot.")


def open_pending(state: AgentState, workflow: str | None = None) -> list[PendingInteraction]:
    """Open pending interactions, optionally only those owned by ``workflow``."""
    return [item for item in state.get("pending_interactions") or [] if item.status == InteractionStatus.OPEN
            and (workflow is None or item.metadata.get("workflow") == workflow)]
