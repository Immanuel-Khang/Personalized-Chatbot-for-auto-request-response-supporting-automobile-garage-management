"""Task Coordinator: execution planning and context preparation for all queries.

Two-phase architecture:
1. Execution Planning: analyze dependencies, group for parallel/sequential execution (NO LLM)
2. Context Preparation: pre-fetch ALL data needed by agents via DataHub (deterministic)

ALL queries go through coordinator now, even single-agent queries.
"""
from app.agent.datahub import DataHub
from app.agent.nodes.router import READ_ONLY, WRITERS, send_all
from app.agent.state import AgentState, fact_value
from app.contracts.schemas import WorkflowStatus

MAX_READ_AGENTS = 3
WRITER_ORDER = 100
NOTE_ORDER = 200
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."


def _analyze_dependencies(state: AgentState, plan: list[dict]) -> dict:
    """Phase 1: Execution Planning - determine what data each agent needs.

    Returns dict mapping agent -> list of required data keys.
    Rule-based analysis for common cases, NO LLM.
    """
    dependencies = {}
    facts = state.get("known_facts") or {}

    for item in plan:
        agent = item["agent"]
        deps = []

        if agent == "contract":
            # Contract always needs: customer info + car_model + decided_price
            # Cross-domain need: MSRP from sales domain (if customer hasn't asked)
            if not fact_value(facts, "car_model"):
                deps.append("infer_car_model")
            else:
                car_model = fact_value(facts, "car_model")
                deps.append(("msrp", car_model))

        elif agent == "appointment":
            # Appointment needs: service_type, appointment_when, customer info
            # May need car_model if it's a showroom visit or contract signing
            if not fact_value(facts, "customer_phone"):
                deps.append("customer_phone")

        elif agent == "knowledge":
            # Knowledge may need car_model for maintenance queries
            interpretation = state.get("conversation_interpretation")
            tasks = [t for t in (interpretation.tasks if interpretation else [])
                    if t.task_id in item.get("task_ids", [])]
            if any(t.action == "maintenance_schedule" for t in tasks):
                car_model = fact_value(facts, "car_model")
                if car_model:
                    deps.append(("product_specs", car_model))

        if deps:
            dependencies[agent] = deps

    return dependencies


def _prepare_context(state: AgentState, dependencies: dict) -> dict:
    """Phase 2: Context Preparation - pre-fetch data via DataHub.

    Returns shared_context dict with fetched data for agents to use.
    Deterministic, NO LLM calls.
    """
    datahub = DataHub()
    shared_context = {}
    facts = state.get("known_facts") or {}
    interpretation = state.get("conversation_interpretation")

    for agent, deps in dependencies.items():
        agent_context = {}

        for dep in deps:
            if dep == "infer_car_model":
                # Infer car_model from presented_cars (sales agent output in history)
                # This is the same logic as contract.py:64-65
                presented = (interpretation.presented_cars if interpretation else None) or []
                if len(presented) == 1:
                    car_model = presented[0]
                    # Fetch MSRP for this inferred model
                    msrp_result = datahub.fetch_msrp(car_model)
                    agent_context["inferred_car_model"] = car_model
                    agent_context["msrp"] = msrp_result.data if msrp_result.state.value == "FOUND" else None

            elif isinstance(dep, tuple) and dep[0] == "msrp":
                # Fetch MSRP for explicit car_model
                car_model = dep[1]
                msrp_result = datahub.fetch_msrp(car_model)
                agent_context["msrp"] = msrp_result.data if msrp_result.state.value == "FOUND" else None
                agent_context["msrp_state"] = msrp_result.state.value

            elif isinstance(dep, tuple) and dep[0] == "product_specs":
                # Fetch full product specs
                car_model = dep[1]
                specs_result = datahub.get_product_specs(car_model)
                agent_context["product_specs"] = specs_result.data if specs_result.state.value == "FOUND" else None

            elif dep == "customer_phone":
                # Validate existing phone or mark as missing
                phone = fact_value(facts, "customer_phone")
                if phone:
                    validation = datahub.validate_phone(phone)
                    agent_context["phone_valid"] = validation.state.value == "FOUND"

        if agent_context:
            shared_context[agent] = agent_context

    return shared_context


def coordinator(state: AgentState) -> dict:
    """Coordinator: execution planning + context preparation for all queries."""
    plan = state.get("plan") or []

    # Separate reads and writers
    reads = [item for item in plan if item["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = [item for item in plan if item["agent"] in WRITERS]
    workflow = state.get("active_workflow")

    # Prefer continuing the writer that already owns the active workflow
    writers.sort(key=lambda item: (not (workflow and workflow.workflow_type == item["agent"]), item["order"]))

    update: dict = {"trace": ["coordinator"]}

    # Build final plan with proper ordering
    final = [{**item, "order": index} for index, item in enumerate(reads)]
    if writers:
        final.append({**writers[0], "order": WRITER_ORDER})

    # Handle deferred second writer
    if len(writers) > 1:
        deferred = writers[1]["agent"]
        update["answers"] = [{"agent": "coordinator", "order": NOTE_ORDER, "needs_human": False,
                              "text": DEFERRED_NOTE.format(label=WRITER_LABEL[deferred])}]
        update["deferred_workflow"] = {
            "workflow_id": f"{state.get('session_id', 'session')}_{deferred}",
            "workflow_type": deferred, "status": WorkflowStatus.WAITING_FOR_USER,
        }

    # Phase 1: Execution Planning - analyze dependencies
    dependencies = _analyze_dependencies(state, final)

    # Phase 2: Context Preparation - pre-fetch data via DataHub
    shared_context = _prepare_context(state, dependencies)

    # Add shared_context to update for agents to access
    update.update(
        plan=final,
        planned_agents=[item["agent"] for item in final],
        shared_context=shared_context,
    )

    return update


def coordinate(state: AgentState):
    """Dispatch to agents with shared_context prepared by coordinator."""
    return send_all(state, state.get("plan") or [])
