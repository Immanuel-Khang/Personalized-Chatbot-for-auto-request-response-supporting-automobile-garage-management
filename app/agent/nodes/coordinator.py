"""Coordinate multi-task turns, including prerequisite-gated tasks.

Independent read-only tasks still fan out together. Tasks with ``depends_on`` wait until
their prerequisites return; dependent writers then receive structured prerequisite results.
At most one contract/appointment writer is selected for a turn.
"""
from app.agent.nodes.router import READ_ONLY, WRITERS, send_all
from app.agent.state import AgentState
from app.contracts.schemas import WorkflowStatus

MAX_READ_AGENTS = 3
WRITER_ORDER = 100
NOTE_ORDER = 200
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."


def _sort_writers(state: AgentState, writers: list[dict]) -> list[dict]:
    workflow = state.get("active_workflow")
    writers = list(writers)
    writers.sort(key=lambda item: (not (workflow and workflow.workflow_type == item["agent"]), item["order"]))
    return writers


def _dependency_ids(tasks: list[dict]) -> set[str]:
    return {dependency for task in tasks for dependency in task.get("depends_on", [])}


def coordinator(state: AgentState) -> dict:
    """Plan the first execution phase; dependent tasks are held until prerequisites finish."""
    plan = list(state.get("plan") or [])
    waiting = [item for item in plan if item.get("depends_on")]
    independent = [item for item in plan if not item.get("depends_on")]

    read_tasks = [item for item in independent if item["agent"] in READ_ONLY]
    required_ids = _dependency_ids(waiting)
    required_reads = [item for item in read_tasks
                      if required_ids.intersection(item.get("task_ids", []))]
    other_reads = [item for item in read_tasks if item not in required_reads]
    # Never drop a prerequisite just because several unrelated read tasks precede it.
    # The cap still limits ordinary fan-out; dependency prerequisites take priority.
    reads = [*required_reads, *other_reads[:max(0, MAX_READ_AGENTS - len(required_reads))]]
    writers = _sort_writers(state, [item for item in plan if item["agent"] in WRITERS])
    selected_writer = writers[0] if writers else None

    # A dependent writer is intentionally held for phase two. An independent writer keeps
    # the original behavior and can run with the independent reads.
    first_phase = reads + ([selected_writer] if selected_writer and not selected_writer.get("depends_on") else [])
    if selected_writer and selected_writer.get("depends_on"):
        waiting = [*waiting]
        if selected_writer not in waiting:
            waiting.append(selected_writer)
    else:
        waiting = [item for item in waiting if item is not selected_writer]

    deferred = writers[1:]
    deferred_ids = {task_id for item in deferred for task_id in item.get("task_ids", [])}
    waiting = [item for item in waiting if not deferred_ids.intersection(item.get("task_ids", []))]
    update: dict = {"trace": ["coordinator"]}
    if deferred:
        item = deferred[0]
        update["answers"] = [{"agent": "coordinator", "order": NOTE_ORDER, "needs_human": False,
                              "text": DEFERRED_NOTE.format(label=WRITER_LABEL[item["agent"]]),
                              "task_ids": item.get("task_ids", [])}]
        update["deferred_workflow"] = {
            "workflow_id": f"{state.get('session_id', 'session')}_{item['agent']}",
            "workflow_type": item["agent"], "status": WorkflowStatus.WAITING_FOR_USER,
        }

    dependency_ids = _dependency_ids(waiting)
    first_phase = [{**item, "order": index, "dependency_task_ids":
                    [task_id for task_id in item.get("task_ids", []) if task_id in dependency_ids]}
                   for index, item in enumerate(first_phase)]
    waiting = [item for item in waiting if item not in first_phase]

    # A dependency that is absent from the first phase or from the waiting chain cannot be
    # satisfied. Keep the original task out of dispatch rather than running it speculatively.
    planned_ids = {task_id for item in [*first_phase, *waiting] for task_id in item.get("task_ids", [])}
    invalid_waiting = []
    for item in waiting:
        missing = [task_id for task_id in item.get("depends_on", []) if task_id not in planned_ids]
        if missing:
            invalid_waiting.append(item)
    if invalid_waiting:
        waiting = [item for item in waiting if item not in invalid_waiting]
        update["answers"] = [*update.get("answers", []), *[
            {"agent": "coordinator", "order": item.get("order", NOTE_ORDER), "needs_human": False,
             "text": "Mình chưa thể tiếp tục bước phụ thuộc vì chưa nhận được kết quả xác minh cần thiết.",
             "task_ids": item.get("task_ids", [])}
            for item in invalid_waiting]]

    update.update(plan=first_phase, planned_agents=[item["agent"] for item in first_phase],
                  waiting_tasks=waiting, dependency_phase="waiting_prerequisites" if waiting else None)
    return update


def _task_result(state: AgentState, task_id: str) -> dict | None:
    return next((result for result in reversed(state.get("task_results") or [])
                 if result.get("task_id") == task_id), None)


def _answer_result(state: AgentState, task_id: str) -> dict | None:
    return next((answer for answer in reversed(state.get("answers") or [])
                 if task_id in (answer.get("task_ids") or [])), None)


def _prerequisite_status(state: AgentState, task_id: str, waiting_ids: set[str]) -> str:
    """Return success, failure, or pending for one prerequisite."""
    result = _task_result(state, task_id)
    if result:
        status = result.get("status")
        return "success" if status == "found" else "failure"
    answer = _answer_result(state, task_id)
    if answer:
        interpretation = state.get("conversation_interpretation")
        task = next((item for item in (interpretation.tasks if interpretation else [])
                     if item.task_id == task_id), None)
        # A writer's clarification is not completion of the requested operation.
        if task and task.domain.value.lower() in WRITERS:
            return "success" if answer.get("executed") else "failure"
        return "success" if (answer.get("text") or "").strip() and not answer.get("needs_human") else "failure"
    return "pending" if task_id in waiting_ids else "failure"


def _matched_vehicle(state: AgentState, item: dict) -> tuple[str | None, list[str]]:
    interpretation = state.get("conversation_interpretation")
    tasks = {task.task_id: task for task in (interpretation.tasks if interpretation else [])}
    target = next((tasks[task_id] for task_id in item.get("task_ids", []) if task_id in tasks), None)
    requested = target.car_model if target else None
    names: list[str] = []
    names_by_dependency: list[list[str]] = []
    dependencies = item.get("depends_on", [])
    if not requested and len(dependencies) == 1:
        prerequisite = tasks.get(dependencies[0])
        requested = prerequisite.car_model if prerequisite else None
    for dependency in dependencies:
        result = _task_result(state, dependency) or {}
        dependency_names: list[str] = []
        for product in result.get("products", []):
            name = product.get("name") if isinstance(product, dict) else str(product)
            if name:
                if name not in dependency_names:
                    dependency_names.append(name)
                if name not in names:
                    names.append(name)
        names_by_dependency.append(dependency_names)
    if len(names_by_dependency) > 1 and all(len(result) == 1 for result in names_by_dependency):
        return (", ".join(names) if item["agent"] == "appointment" else None), names
    exact = next((name for name in names if requested and name.casefold() == requested.casefold()), None)
    if exact:
        return exact, names
    return (names[0] if len(names) == 1 and not requested else None), names


def coordinator_resume(state: AgentState) -> dict:
    """Resolve completed prerequisite results and release only safe dependent tasks."""
    waiting = list(state.get("waiting_tasks") or [])
    waiting_ids = {task_id for item in waiting for task_id in item.get("task_ids", [])}
    ready: list[dict] = []
    remaining: list[dict] = []
    update: dict = {"trace": ["coordinator_resume"]}

    for item in waiting:
        statuses = [_prerequisite_status(state, dependency, waiting_ids)
                    for dependency in item.get("depends_on", [])]
        if "failure" in statuses:
            similar = next((_task_result(state, dependency) for dependency, status in zip(
                item.get("depends_on", []), statuses) if status == "failure"
                and (_task_result(state, dependency) or {}).get("status") == "similar"), None)
            if similar and item["agent"] == "appointment":
                names = [product.get("name", "") for product in similar.get("products", [])
                         if isinstance(product, dict) and product.get("name")]
                if names:
                    update.setdefault("answers", []).append({
                        "agent": "coordinator", "order": item.get("order", 0), "needs_human": False,
                        "text": "Mình chưa tìm thấy đúng mẫu bạn nêu. Mẫu gần nhất là " + ", ".join(names)
                               + ". Bạn có muốn đặt lịch xem mẫu này không?",
                        "task_ids": item.get("task_ids", []),
                    })
            continue  # The prerequisite answer remains in state; never run a writer speculatively.
        if "pending" in statuses:
            remaining.append(item)
            continue

        dispatched = {**item, "depends_on": [],
                      "order": WRITER_ORDER if item["agent"] in WRITERS else item.get("order", 0)}
        vehicle, names = _matched_vehicle(state, item)
        if item["agent"] in WRITERS and names and not vehicle:
            prompt = ("Mình tìm thấy nhiều mẫu xe phù hợp: " + ", ".join(names)
                      + ". Bạn chọn giúp mình mẫu muốn đến xem nhé." if item["agent"] == "appointment" else
                      "Mình tìm thấy nhiều mẫu xe phù hợp: " + ", ".join(names)
                      + ". Bạn chọn mẫu muốn làm hợp đồng nhé.")
            update.setdefault("answers", []).append({
                "agent": "coordinator", "order": item.get("order", 0), "needs_human": False,
                "text": prompt,
                "task_ids": item.get("task_ids", []),
            })
            continue
        if vehicle:
            dispatched["verified_vehicle"] = vehicle
        ready.append(dispatched)

    # Mark prerequisite Sales tasks so they return a structured match result, not just prose.
    needed_ids = _dependency_ids(remaining)
    for item in ready:
        item["dependency_task_ids"] = [task_id for task_id in item.get("task_ids", []) if task_id in needed_ids]
    update.update(plan=ready, planned_agents=[item["agent"] for item in ready], waiting_tasks=remaining,
                  dependency_phase=("waiting_prerequisites" if remaining else "dependents") if ready else None)
    return update


def coordinate(state: AgentState):
    plan = state.get("plan") or []
    return send_all(state, plan) if plan else "validation"


def dispatch_resumed(state: AgentState):
    """Dispatch a released phase, or continue to validation if every dependency was blocked."""
    plan = state.get("plan") or []
    return send_all(state, plan) if plan else "validation"
