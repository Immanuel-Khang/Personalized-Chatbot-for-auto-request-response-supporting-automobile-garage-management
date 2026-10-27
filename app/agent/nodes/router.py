"""Plan specialist execution exclusively from canonical interpretation and state."""
from langgraph.types import Send

from app.agent.nodes.common import step
from app.agent.nodes.service_type import is_service_inquiry
from app.agent.state import AgentState
from app.contracts.schemas import Intent, TaskRelationship, WorkflowStatus

AGENTS = {
    Intent.CONTRACT: "contract", Intent.APPOINTMENT: "appointment",
    Intent.AFTER_SALES: "after_sales", Intent.MAINTENANCE: "knowledge",
    Intent.SALES: "sales", Intent.KNOWLEDGE: "knowledge",
    Intent.GREETING: "conversation", Intent.OTHER: "conversation",
}
READ_ONLY = ("sales", "knowledge", "after_sales", "conversation")
WRITERS = ("contract", "appointment")
MAX_READ_AGENTS = 3
WRITER_ORDER = 100
NOTE_ORDER = 200
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."
CONTRACT_QUESTION = ("giấy tờ", "thủ tục", "hồ sơ", "cần chuẩn bị", "cần những gì", "điều kiện")
BOOKING_FACTS = {"service_type", "appointment_when", "when_text", "service_detail"}
BOOKING_SLOTS = BOOKING_FACTS
CANCEL_WORDS = ("hủy", "huỷ", "không đặt", "không muốn đặt", "không cần đặt", "không book")
AFFIRM = ("được", "ok", "oke", "okay", "ừ", "ừm", "vâng", "dạ", "có", "đồng ý", "tiếp tục", "đúng rồi", "uh")


def _affirm(text: str) -> bool:
    return text.casefold().strip().rstrip(".!?, ") in AFFIRM


def _facts(state: AgentState) -> dict:
    return {key: fact.value for key, fact in (state.get("known_facts") or {}).items()}


def _is_cancel(state: AgentState, tasks) -> bool:
    if state.get("safety"):
        return False
    text = state.get("user_text", "").casefold().strip().rstrip(".!?")
    workflow = state.get("active_workflow")
    return any(word in text for word in CANCEL_WORDS) or any(
        task.relationship == TaskRelationship.CANCEL_TASK for task in tasks
    ) or bool(workflow and text in ("thôi", "không cần", "không muốn", "không, cảm ơn"))


def _relationship_pending(task) -> bool:
    return task.relationship in {
        TaskRelationship.ANSWER_TO_QUESTION, TaskRelationship.ACCEPT_OFFER,
        TaskRelationship.REJECT_OFFER, TaskRelationship.CONTINUE_TASK,
    }


def _contract_inquiry(task) -> bool:
    return task.domain in (Intent.CONTRACT, Intent.SALES) and any(
        phrase in task.text.casefold() for phrase in CONTRACT_QUESTION
    )


def _open_tasks(state: AgentState):
    interpretation = state.get("conversation_interpretation")
    return list(interpretation.tasks) if interpretation else []


def router(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "router")}
    text = state.get("user_text", "")
    interpretation = state.get("conversation_interpretation")
    tasks = _open_tasks(state)
    workflow = state.get("active_workflow")
    facts = _facts(state)

    if state.get("safety"):
        update.update(plan=[{"agent": "knowledge", "text": text, "order": 0}], planned_agents=["knowledge"])
        return update

    if _is_cancel(state, tasks):
        pending = [item.model_copy(update={"status": "REJECTED"}) for item in state.get("pending_interactions", [])
                   if getattr(item.status, "value", item.status) == "OPEN"]
        cleared = {key: fact for key, fact in (state.get("known_facts") or {}).items() if key not in BOOKING_FACTS}
        update.update(
            plan=[{"agent": "conversation", "text": text, "order": 0}],
            planned_agents=["conversation"],
            route_reason="cancel",
            pending_interactions=pending,
            known_facts=cleared,
            active_workflow=None,
        )
        return update

    if not tasks:
        clarification = "clarify" if interpretation and interpretation.uncertain else None
        if not workflow and is_service_inquiry(text):
            update.update(plan=[{"agent": "conversation", "text": text, "order": 0}],
                          planned_agents=["conversation"], route_reason="service_inquiry")
        else:
            update.update(plan=[{"agent": "conversation", "text": text, "order": 0}],
                          planned_agents=["conversation"], route_reason=clarification)
        return update

    grouped: dict[str, dict] = {}
    booking_active = bool(workflow and workflow.workflow_type == "appointment")
    for task in tasks:
        agent = AGENTS.get(task.domain, "conversation")
        if _contract_inquiry(task) and not (workflow and workflow.workflow_type == "contract"):
            agent = "conversation"
        if task.domain == Intent.MAINTENANCE and booking_active and not any(
            word in task.text.casefold() for word in ("bao lâu", "bao nhiêu", "khi nào", "chi phí", "tại sao")
        ):
            continue
        group = grouped.setdefault(agent, {"agent": agent, "texts": [], "tasks": []})
        if task.text and task.text not in group["texts"]:
            group["texts"].append(task.text)
        group["tasks"].append(task)

    groups = [{**item, "text": ", ".join(item.pop("texts"))} for item in grouped.values()]
    reads = [group for group in groups if group["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = sorted((group for group in groups if group["agent"] in WRITERS),
                     key=lambda group: WRITERS.index(group["agent"]))

    active_agent = None
    if workflow and workflow.status in (WorkflowStatus.ACTIVE, WorkflowStatus.WAITING_FOR_USER):
        active_agent = workflow.workflow_type if workflow.workflow_type in WRITERS else None
    writer = writers[0] if writers else None
    deferred = writers[1] if len(writers) > 1 else None

    if writer is None and active_agent and (
        bool(interpretation and (interpretation.slot_updates or interpretation.answers_pending_question))
        or any(_relationship_pending(task) for task in tasks)
    ):
        writer = {"agent": active_agent, "text": text}
    elif writer is None and not reads and active_agent and interpretation and interpretation.accepts_offer:
        writer = {"agent": active_agent, "text": text}

    if not writer and not reads:
        if not active_agent and not workflow and is_service_inquiry(text):
            update.update(plan=[{"agent": "conversation", "text": text, "order": 0}],
                          planned_agents=["conversation"], route_reason="service_inquiry")
        else:
            update.update(plan=[{"agent": "conversation", "text": text, "order": 0}],
                          planned_agents=["conversation"], route_reason="clarify" if active_agent else None)
        return update

    plan = [{"agent": group["agent"], "text": group["text"], "order": order}
            for order, group in enumerate(reads)]
    if writer:
        plan.append({"agent": writer["agent"], "text": writer["text"], "order": WRITER_ORDER})
    if deferred:
        deferred_agent = deferred["agent"]
        update["answers"] = [{"agent": "router", "order": NOTE_ORDER, "needs_human": False,
                              "text": DEFERRED_NOTE.format(label=WRITER_LABEL[deferred_agent])}]
        update["active_workflow"] = {
            "workflow_id": f"{state.get('session_id', 'session')}_{deferred_agent}",
            "workflow_type": deferred_agent, "status": WorkflowStatus.WAITING_FOR_USER,
            "collected_facts": facts,
        }
    elif writer and (not workflow or workflow.workflow_type != writer["agent"]):
        update["active_workflow"] = {
            "workflow_id": f"{state.get('session_id', 'session')}_{writer['agent']}",
            "workflow_type": writer["agent"], "status": WorkflowStatus.ACTIVE,
            "collected_facts": facts,
        }
    if interpretation and any(_contract_inquiry(task) for task in tasks):
        update["route_reason"] = "contract_inquiry"
    update.update(plan=plan, planned_agents=[item["agent"] for item in plan])
    return update


def dispatch(state: AgentState):
    plan = state.get("plan", [])
    if not plan:
        return "compose"
    return [Send(item["agent"], {**state, "task_text": item["text"], "task_order": item["order"]})
            for item in plan]
