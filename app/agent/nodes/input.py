"""Input / Load Context: normalise persisted conversation state before interpretation.

Checkpoints may return pydantic models or plain dicts; every later node relies on
models. ``build_context`` assembles exactly what preprocessing sends to the LLM.
"""
from app.agent.state import AgentState
from app.contracts.schemas import (
    ActiveWorkflow,
    ConversationContext,
    InteractionStatus,
    KnownFact,
    PendingInteraction,
)

AVAILABLE_ACTIONS = [
    {"action": "search_catalog", "domain": "SALES", "description": "Search vehicles by explicit criteria"},
    {"action": "show_catalog", "domain": "SALES", "description": "Show vehicle catalog on explicit request"},
    {"action": "product_detail", "domain": "SALES", "description": "Show a previously displayed product"},
    {"action": "product_specs", "domain": "SALES",
     "description": "Specs of a catalog vehicle (engine, transmission, seats, fuel, price)"},
    {"action": "book_appointment", "domain": "APPOINTMENT",
     "description": "Book service or showroom visit",
     "required_fields": ["service_type", "appointment_when", "customer_phone"]},
    {"action": "answer_knowledge", "domain": "KNOWLEDGE", "description": "General technical/repair/safety questions, not specs of a catalog model"},
    {"action": "maintenance_schedule", "domain": "MAINTENANCE", "description": "Maintenance milestones and costs"},
    {"action": "show_service_history", "domain": "AFTER_SALES", "description": "Show the customer's service history"},
    {"action": "start_contract", "domain": "CONTRACT", "description": "Begin purchase contract workflow",
     "required_fields": ["customer_name", "customer_phone", "customer_address", "car_model", "decided_price"]},
    {"action": "request_discount", "domain": "DISCOUNT", "description": "Customer bargains; manager must approve"},
    {"action": "general_conversation", "domain": "OTHER",
     "description": "Greetings, clarification, out-of-scope or general conversation"},
]


def _model(cls, value):
    return value if value is None or isinstance(value, cls) else cls.model_validate(value)


def load_context(state: AgentState) -> dict:
    pending = [_model(PendingInteraction, item) for item in state.get("pending_interactions") or []]
    facts = {key: _model(KnownFact, value) for key, value in (state.get("known_facts") or {}).items()}
    return {
        # Closed interactions have no further use; keeping them only grows the prompt.
        "pending_interactions": [item for item in pending if item.status == InteractionStatus.OPEN],
        "known_facts": facts,
        "active_workflow": _model(ActiveWorkflow, state.get("active_workflow")),
        "trace": ["input"],
    }


def build_context(state: AgentState, canonical_slots: dict[str, str]) -> ConversationContext:
    return ConversationContext(
        current_message={"id": None, "text": state.get("user_text", ""), "timestamp": None},
        history=state.get("history", []),
        known_facts=state.get("known_facts") or {},
        pending_interactions=state.get("pending_interactions") or [],
        active_workflow=state.get("active_workflow"),
        shown_products=state.get("selected_entities") or [],
        canonical_slots=canonical_slots,
        available_actions=AVAILABLE_ACTIONS,
    )
