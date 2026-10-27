"""Routing Node: map validated tasks to specialists.

Reads only the structured interpretation and workflow state; it never re-parses the
customer's text. One planned specialist is dispatched directly; several go through the
Task Coordinator, which orders them and enforces the single-writer rule.
"""
from langgraph.types import Send

from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import InteractionStatus, Intent, TaskRelationship, WorkflowStatus

AGENTS = {
    Intent.CONTRACT: "contract", Intent.APPOINTMENT: "appointment",
    Intent.AFTER_SALES: "after_sales", Intent.MAINTENANCE: "knowledge",
    Intent.SALES: "sales", Intent.DISCOUNT: "sales", Intent.KNOWLEDGE: "knowledge",
    Intent.GREETING: "conversation", Intent.OTHER: "conversation",
}
READ_ONLY = ("sales", "knowledge", "after_sales", "conversation")
WRITERS = ("contract", "appointment")
BOOKING_FACTS = {"service_type", "appointment_when", "service_detail"}
CONTINUING = {TaskRelationship.ANSWER_TO_QUESTION, TaskRelationship.ACCEPT_OFFER,
              TaskRelationship.CONTINUE_TASK, TaskRelationship.CORRECT_PREVIOUS_INFO}


def _active_writer(state: AgentState) -> str | None:
    workflow = state.get("active_workflow")
    if workflow and workflow.workflow_type in WRITERS and workflow.status in (
            WorkflowStatus.ACTIVE, WorkflowStatus.WAITING_FOR_USER):
        return workflow.workflow_type
    return None


def _offer_workflow(state: AgentState, task) -> str | None:
    """Workflow of the pending interaction this task answers, if any."""
    open_items = [item for item in state.get("pending_interactions") or [] if item.status == InteractionStatus.OPEN]
    match = next((item for item in open_items if item.interaction_id == task.referenced_interaction_id), None)
    match = match or (open_items[-1] if open_items else None)
    workflow = match.metadata.get("workflow") if match else None
    return workflow if workflow in WRITERS else None


def _route_task(state: AgentState, task) -> tuple[str, str | None]:
    """(agent, conversation reason) for one task."""
    if task.inquiry_only:
        if task.domain == Intent.CONTRACT:
            return "conversation", "contract_inquiry"
        if task.domain in (Intent.APPOINTMENT, Intent.MAINTENANCE, Intent.KNOWLEDGE, Intent.AFTER_SALES):
            return "conversation", "service_inquiry"
    if task.relationship == TaskRelationship.REJECT_OFFER:
        return "conversation", "decline"
    if task.domain == Intent.GREETING:
        return "conversation", "greeting"
    if task.domain == Intent.OTHER:
        if task.relationship in CONTINUING and (writer := _offer_workflow(state, task) or _active_writer(state)):
            return writer, None
        return "conversation", "clarify" if task.requires_clarification else None
    return AGENTS[task.domain], None


def _cancel(state: AgentState) -> dict:
    pending = [item.model_copy(update={"status": InteractionStatus.REJECTED})
               if item.status == InteractionStatus.OPEN else item for item in state.get("pending_interactions") or []]
    workflow = state.get("active_workflow")
    facts = dict(state.get("known_facts") or {})
    if workflow and workflow.workflow_type == "appointment":
        facts = {key: fact for key, fact in facts.items() if key not in BOOKING_FACTS}
    return {"plan": [{"agent": "conversation", "text": state.get("user_text", ""), "order": 0}],
            "planned_agents": ["conversation"], "route_reason": "cancel",
            "pending_interactions": pending, "known_facts": facts, "active_workflow": None}


def router(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "router")}
    text = state.get("user_text", "")
    interpretation = state.get("conversation_interpretation")
    tasks = list(interpretation.tasks) if interpretation else []

    if state.get("safety"):  # policy guard flagged a safety risk the bot may still answer
        plan = [{"agent": "knowledge", "text": text, "order": 0}]
        return {**update, "plan": plan, "planned_agents": ["knowledge"], "route_reason": "safety"}
    if any(task.relationship == TaskRelationship.CANCEL_TASK for task in tasks):
        return {**update, **_cancel(state)}

    plan: list[dict] = []
    reason = "clarify" if not tasks or (interpretation and interpretation.uncertain) else None
    for order, task in enumerate(tasks):
        agent, task_reason = _route_task(state, task)
        reason = reason or task_reason
        existing = next((item for item in plan if item["agent"] == agent), None)
        if existing:  # one specialist call per agent; it receives all of its task texts
            existing["text"] = f"{existing['text']}, {task.text}" if task.text not in existing["text"] else existing["text"]
            existing["task_ids"].append(task.task_id)
        else:
            plan.append({"agent": agent, "text": task.text or text, "order": order, "task_ids": [task.task_id]})
    if not plan:
        plan = [{"agent": "conversation", "text": text, "order": 0, "task_ids": []}]
    return {**update, "plan": plan, "planned_agents": [item["agent"] for item in plan], "route_reason": reason}


def send_all(state: AgentState, plan: list[dict]) -> list[Send]:
    return [Send(item["agent"], {**state, "task_text": item["text"], "task_order": item["order"],
                                 "task_ids": item.get("task_ids", [])}) for item in plan]


def dispatch(state: AgentState):
    """Single specialist -> run it directly; several tasks -> Task Coordinator."""
    plan = state.get("plan") or []
    return "coordinator" if len(plan) > 1 else send_all(state, plan)
