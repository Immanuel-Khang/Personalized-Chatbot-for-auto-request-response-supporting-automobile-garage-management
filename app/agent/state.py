import operator
from typing import Annotated, TypedDict

from app.contracts.schemas import (
    ActiveWorkflow,
    ConversationInterpretation,
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
    # Previous user/assistant messages only; the current message is in user_text.
    history: list[dict]
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
    # Pronoun adaptation: how to address customer and how bot refers to itself
    customer_pronoun: str | None  # 'anh', 'chị', or None (use neutral 'bạn')
    bot_pronoun: str | None  # 'em' (when customer uses anh/chị), or 'mình'/'shop' (neutral)


def answer(state: AgentState, agent: str, text: str = "", needs_human: bool = False,
           handover_reason: str | None = None, executed: bool = False) -> dict:
    """Phần trả lời của một specialist; validation kiểm tra rồi compose ghép theo task_order.
    executed=True chỉ khi service xác nhận hành động (đặt lịch/hợp đồng) thành công trong lượt này."""
    return {"agent": agent, "order": state.get("task_order", 0), "text": text,
            "needs_human": needs_human, "handover_reason": handover_reason, "executed": executed}


# Load Context (nodes/input.py) turns checkpointed facts/pending/workflow into models, so nodes read attributes.
def fact_value(facts: dict | None, key: str):
    return getattr((facts or {}).get(key), "value", None)


def pronouns(state: AgentState) -> tuple[str, str]:
    """(customer, bot) address terms: anh/chị-em once the customer used them, else neutral bạn-mình."""
    customer, bot = state.get("customer_pronoun"), state.get("bot_pronoun")
    if customer in ("anh", "chị"):
        return customer, bot or "em"
    return "bạn", bot or "mình"


def pronoun_vars(state: AgentState) -> dict[str, str]:
    """Template kwargs: customer, bot, customer_cap, bot_cap."""
    customer, bot = pronouns(state)
    return {"customer": customer, "bot": bot, "customer_cap": customer.capitalize(), "bot_cap": bot.capitalize()}


def pronoun_instruction(customer: str, bot: str) -> str:
    if customer in ("anh", "chị"):
        return f"LUÔN dùng '{customer}' (khách) với '{bot}' (bot/shop). KHÔNG dùng bạn/mình."
    return f"LUÔN dùng '{bot}' hoặc 'shop' (bot) với '{customer}' (khách). KHÔNG dùng anh/chị-em."


def open_pending(state: AgentState, workflow: str | None = None) -> list[PendingInteraction]:
    """Open pending interactions, optionally only those owned by ``workflow``."""
    return [item for item in state.get("pending_interactions") or [] if item.status == InteractionStatus.OPEN
            and (workflow is None or item.metadata.get("workflow") == workflow)]
