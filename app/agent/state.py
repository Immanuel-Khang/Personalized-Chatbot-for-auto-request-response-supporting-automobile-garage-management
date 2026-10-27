import operator
from typing import Annotated, TypedDict

from app.contracts.schemas import (
    ActiveWorkflow,
    ConversationInterpretation,
    KnownFact,
    PendingInteraction,
)


class AgentState(TypedDict, total=False):
    session_id: str
    customer_id: int | None
    user_text: str
    mode: str
    # Previous user/assistant messages only; the current message is in user_text.
    history: list[dict]
    complexity: str
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
