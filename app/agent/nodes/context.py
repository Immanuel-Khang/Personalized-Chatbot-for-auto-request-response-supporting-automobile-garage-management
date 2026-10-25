"""Assemble the canonical context supplied to turn interpretation."""
from app.agent.state import AgentState
from app.contracts.schemas import (
    ActiveWorkflow,
    ConversationContext,
    InteractionStatus,
    PendingInteraction,
)


def assemble_context(state: AgentState, message_id: int | None = None) -> ConversationContext:
    pending = state.get("pending_interactions", []) or []
    interactions = [
        item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        for item in pending
    ]
    workflow = state.get("active_workflow")
    if workflow is not None and not isinstance(workflow, ActiveWorkflow):
        workflow = ActiveWorkflow.model_validate(workflow)
    return ConversationContext(
        schema_version="1.0",
        current_message={"id": message_id, "text": state.get("user_text", ""), "timestamp": None},
        history=state.get("history", []),
        known_facts=state.get("known_facts", {}) or {},
        pending_interactions=[item for item in interactions if item.status == InteractionStatus.OPEN],
        active_workflow=workflow,
        selected_entities=state.get("selected_entities", []) or [],
        available_actions=_define_available_actions(),
    )


def _define_available_actions() -> list[dict]:
    return [
        {"action": "search_catalog", "domain": "SALES", "description": "Search vehicles by explicit criteria",
         "required_fields": [], "optional_fields": ["car_model", "budget_vnd", "intended_use", "payload_tons"]},
        {"action": "show_catalog", "domain": "SALES", "description": "Show vehicle catalog on explicit request",
         "required_fields": [], "optional_fields": []},
        {"action": "product_detail", "domain": "SALES", "description": "Show a previously displayed product",
         "required_fields": ["product_id"], "optional_fields": []},
        {"action": "book_appointment", "domain": "APPOINTMENT", "description": "Book service, showroom, or test-drive appointment",
         "required_fields": ["service_type", "appointment_when", "customer_phone"], "optional_fields": ["service_detail"]},
        {"action": "answer_knowledge", "domain": "KNOWLEDGE", "description": "Answer technical or service questions",
         "required_fields": ["question"], "optional_fields": []},
        {"action": "show_service_history", "domain": "AFTER_SALES", "description": "Show authorized customer service history",
         "required_fields": ["customer_id"], "optional_fields": []},
        {"action": "start_contract", "domain": "CONTRACT", "description": "Begin purchase contract workflow",
         "required_fields": ["customer_phone", "decided_price"], "optional_fields": ["customer_name", "customer_address"]},
        {"action": "general_conversation", "domain": "OTHER", "description": "Handle greetings, clarification, or general conversation",
         "required_fields": [], "optional_fields": []},
    ]
