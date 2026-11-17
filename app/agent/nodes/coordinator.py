"""Task Coordinator: execution semantics for multi-task turns.

- Tasks that do not need each other's result run in parallel (one wave). The Preprocessing LLM states what a task
  needs (``TaskInterpretation.depends_on``); the coordinator only follows that. A task whose dependencies are not
  finished runs in a later wave, and receives the answers of the finished agents as shared context
  (``prior_results``, rendered by ``state.prior_block``), so it works with what they found.
- At most one writer (contract | appointment) runs per turn; a second writer is deferred
  as a WAITING_FOR_USER workflow and announced with a short note.
- Writers run after read-only tasks in the composed reply, so "tìm xe số 1 và đặt lịch
  xem xe" answers about the vehicle first, then continues the booking.
The graph returns to this node after each wave (``next_step``) until every planned agent has run.
"""
from app.agent.llm import generate_structured
from app.agent.nodes.router import READ_ONLY, WRITERS, send_all
from app.agent.state import AgentState
from app.contracts.schemas import CarChoice, ConversationInterpretation, WorkflowStatus

CAR_CHOICE_PROMPT = """Khách nhắn một tin gồm nhiều yêu cầu; một yêu cầu cần biết khách chọn xe nào trong danh sách mà \
agent khác vừa giới thiệu ngay trong lượt này. Dựa vào lời khách (số thứ tự, "rẻ nhất", "đắt nhất", "xe đầu tiên", \
"xe vừa tìm được"...) và nội dung agent đã trả lời, chọn đúng MỘT xe trong danh sách ứng viên. Nếu khách chưa chọn một \
xe cụ thể hoặc nhiều xe đều phù hợp như nhau, trả None; không đoán."""

MAX_READ_AGENTS = 3
WRITER_ORDER = 100
NOTE_ORDER = 200
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."


def _task_ids(items: list[dict]) -> set[str]:
    return {task_id for item in items for task_id in item.get("task_ids") or []}


def _waiting_for(state: AgentState, item: dict) -> set[str]:
    """task_ids of other planned tasks that ``item`` needs, which have not run yet."""
    interpretation = state.get("conversation_interpretation")
    plan = state.get("plan") or []
    mine = set(item.get("task_ids") or [])
    wanted = {dep for task in (interpretation.tasks if interpretation else []) if task.task_id in mine
              for dep in task.depends_on} - mine
    sent = {agent for agent in state.get("dispatched") or []}
    done = _task_ids([p for p in plan if p["agent"] in sent])
    return (wanted & _task_ids(plan)) - done  # a dependency outside the plan (dropped) cannot be waited for


def _pending(state: AgentState) -> list[dict]:
    sent = set(state.get("dispatched") or [])
    return [item for item in state.get("plan") or [] if item["agent"] not in sent]


def _wave(state: AgentState) -> list[dict]:
    pending = _pending(state)
    ready = [item for item in pending if not _waiting_for(state, item)]
    return ready or pending[:1]  # a dependency cycle must not stall the turn


def _prior(state: AgentState) -> list[dict]:
    answers = sorted(state.get("answers") or [], key=lambda a: a.get("order", 0))
    return [{"agent": a["agent"], "text": a["text"], "cars": a.get("cars") or []} for a in answers
            if (a.get("text") or "").strip() and not a.get("needs_human")]


def _resolve_cars(state: AgentState, wave: list[dict], prior: list[dict]) -> ConversationInterpretation | None:
    """A writer task that depends on a list built in this same turn ("xe rẻ nhất", "xe số 2") could not name its car
    at preprocessing time, because the list did not exist yet. Now that it does, the LLM judges which car the
    customer meant; the code only accepts a car that the finished agents actually presented."""
    interpretation = state.get("conversation_interpretation")
    cars = list(dict.fromkeys(car for item in prior for car in item["cars"]))
    if not interpretation or not cars:
        return None
    wanted = {task_id for item in wave if item["agent"] in WRITERS for task_id in item.get("task_ids") or []}
    tasks, changed = [], False
    for task in interpretation.tasks:
        if task.task_id in wanted and task.depends_on and not task.car_model:
            choice = generate_structured(
                CAR_CHOICE_PROMPT,
                f"Tin nhắn của khách: {state.get('user_text', '')}\nYêu cầu cần chọn xe: {task.text}\n"
                f"Các xe vừa được giới thiệu (đúng thứ tự): {cars}\n"
                + "\n".join(f"- ({item['agent']}): {item['text']}" for item in prior), CarChoice)
            if choice and choice.car_model in cars:
                task, changed = task.model_copy(update={"car_model": choice.car_model}), True
        tasks.append(task)
    return interpretation.model_copy(update={"tasks": tasks}) if changed else None


def _plan(state: AgentState) -> dict:
    plan = state.get("plan") or []
    reads = [item for item in plan if item["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = [item for item in plan if item["agent"] in WRITERS]
    workflow = state.get("active_workflow")
    # Prefer continuing the writer that already owns the active workflow.
    writers.sort(key=lambda item: (not (workflow and workflow.workflow_type == item["agent"]), item["order"]))

    update: dict = {}
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


def coordinator(state: AgentState) -> dict:
    update: dict = {"trace": ["coordinator"]}
    if not state.get("dispatched"):  # first visit this turn: fix the plan
        update.update(_plan(state))
        state = {**state, **update}
    wave = _wave(state)
    prior = _prior(state)
    if (resolved := _resolve_cars(state, wave, prior)) is not None:
        update["conversation_interpretation"] = resolved
    return {**update, "wave": wave, "dispatched": [item["agent"] for item in wave], "prior_results": prior}


def coordinate(state: AgentState):
    return send_all(state, state.get("wave") or [])


def next_step(state: AgentState) -> str:
    """After a specialist: back to the coordinator while planned agents are left (multi-task turn), else validation."""
    plan = state.get("plan") or []
    return "coordinator" if len(plan) > 1 and _pending(state) else "validation"
