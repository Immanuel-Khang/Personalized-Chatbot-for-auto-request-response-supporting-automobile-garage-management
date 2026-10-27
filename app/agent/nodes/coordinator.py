"""Task Coordinator: execution semantics for multi-task turns.

- Read-only specialists run in parallel, in the order the customer asked.
- At most one writer (contract | appointment) runs per turn; a second writer is deferred
  as a WAITING_FOR_USER workflow and announced with a short note.
- Writers run after read-only tasks in the composed reply, so "tìm xe số 1 và đặt lịch
  xem xe" answers about the vehicle first, then continues the booking.
"""
from app.agent.nodes.common import step
from app.agent.nodes.router import READ_ONLY, WRITERS, send_all
from app.agent.state import AgentState
from app.contracts.schemas import WorkflowStatus

MAX_READ_AGENTS = 3
WRITER_ORDER = 100
NOTE_ORDER = 200
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."


def coordinator(state: AgentState) -> dict:
    plan = state.get("plan") or []
    reads = [item for item in plan if item["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = [item for item in plan if item["agent"] in WRITERS]
    workflow = state.get("active_workflow")
    # Prefer continuing the writer that already owns the active workflow.
    writers.sort(key=lambda item: (not (workflow and workflow.workflow_type == item["agent"]), item["order"]))

    update: dict = {"trace": step(state, "coordinator")}
    final = [{**item, "order": index} for index, item in enumerate(reads)]
    if writers:
        final.append({**writers[0], "order": WRITER_ORDER})
    if len(writers) > 1:
        deferred = writers[1]["agent"]
        update["answers"] = [{"agent": "coordinator", "order": NOTE_ORDER, "needs_human": False,
                              "text": DEFERRED_NOTE.format(label=WRITER_LABEL[deferred])}]
        update["deferred_workflow"] = {
            "workflow_id": f"{state.get('session_id', 'session')}_{deferred}",
            "workflow_type": deferred, "status": WorkflowStatus.WAITING_FOR_USER,
        }
    update.update(plan=final, planned_agents=[item["agent"] for item in final])
    return update


def coordinate(state: AgentState):
    return send_all(state, state.get("plan") or [])
