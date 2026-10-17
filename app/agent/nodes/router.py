"""[TRACK B] Intent Router = bộ lập kế hoạch, nơi duy nhất chọn agent.
- Agent chỉ đọc (sales, knowledge, after_sales): chạy song song, mỗi agent nhận đúng phần câu của mình.
- Agent ghi dữ liệu (contract, appointment): tối đa MỘT mỗi lượt, cái còn lại hoãn sang lượt sau.
- Không có agent nào: câu trả lời mặc định (chào / ngoài phạm vi / đã hủy)."""
from langgraph.types import Send

from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent

AGENTS = {
    Intent.CONTRACT.value: "contract", Intent.APPOINTMENT.value: "appointment",
    Intent.AFTER_SALES.value: "after_sales", Intent.MAINTENANCE.value: "knowledge",
    Intent.SALES.value: "sales", Intent.KNOWLEDGE.value: "knowledge",
    Intent.GREETING.value: "conversation", Intent.OTHER.value: "conversation",
}
READ_ONLY = ("sales", "knowledge", "after_sales", "conversation")
WRITERS = ("contract", "appointment")  # thứ tự ưu tiên khi cùng xuất hiện
WRITER_INTENT = {"contract": Intent.CONTRACT.value, "appointment": Intent.APPOINTMENT.value}
WRITER_LABEL = {"contract": "hoàn tất hợp đồng", "appointment": "đặt lịch hẹn"}
MAX_READ_AGENTS = 3
WRITER_ORDER = 100  # câu hỏi slot của agent ghi dữ liệu luôn nằm cuối câu trả lời
NOTE_ORDER = 200
DEFERRED_NOTE = "Sau bước này mình sẽ hỗ trợ tiếp phần {label} cho bạn nhé."
CANCEL = ["hủy", "huỷ", "không đặt", "không muốn đặt", "không cần đặt", "không book"]
BOOKING_SLOTS = {"service_type", "appointment_when", "when_text"}
QUESTION = ["bao lâu", "bao nhiêu", "khi nào", "tại sao", "vì sao", "mốc", "chi phí", "cần làm gì"]


def _cancelled(state: AgentState) -> bool:
    if state.get("safety"):
        return False
    text = state.get("user_text", "").lower().strip().rstrip(".!?")
    has_flow = bool(state.get("pending_intent") or state.get("active_flow"))
    return any(k in text for k in CANCEL) or (has_flow and text in ("thôi", "không cần", "không muốn", "không, cảm ơn"))


def _groups(state: AgentState) -> tuple[list[dict], bool]:
    """Gộp task theo agent, giữ thứ tự xuất hiện. Trả (groups, có vế là câu trả lời slot cho appointment)."""
    tasks = state.get("tasks", [])
    booking = (state.get("active_flow") == "appointment"
               or any(t["intent"] == Intent.APPOINTMENT.value for t in tasks))
    groups: dict[str, dict] = {}
    slot_answer = False
    for t in tasks:
        agent = AGENTS.get(t["intent"])
        if not agent:
            continue
        # 'Đặt lịch bảo dưỡng' / trả lời 'Bảo dưỡng' là loại dịch vụ, không phải câu hỏi bảo dưỡng
        if (t["intent"] == Intent.MAINTENANCE.value and booking
                and not any(k in t["text"].lower() for k in QUESTION)):
            slot_answer = True
            continue
        group = groups.setdefault(agent, {"agent": agent, "texts": []})
        if t["text"] not in group["texts"]:
            group["texts"].append(t["text"])
    return [{"agent": g["agent"], "text": ", ".join(g["texts"])} for g in groups.values()], slot_answer


def _route_to_conversation(state: AgentState, text: str) -> dict:
    """Route to conversation agent instead of using fixed reply."""
    return {
        "plan": [{"agent": "conversation", "text": text, "order": 0}],
        "planned_agents": ["conversation"]
    }


def router(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "router")}
    text = state.get("user_text", "")

    if state.get("safety"):  # cảnh báo an toàn luôn đi một mình, đọc cả câu
        update.update(plan=[{"agent": "knowledge", "text": text, "order": 0}], planned_agents=["knowledge"])
        return update

    if _cancelled(state):
        update.update(pending_intent=None, pending_text=None, active_flow=None,
                      slots={k: v for k, v in state.get("slots", {}).items() if k not in BOOKING_SLOTS})
        # Route cancel to conversation agent instead of fixed reply
        update.update(_route_to_conversation(state, text))
        return update

    groups, slot_answer = _groups(state)
    reads = [g for g in groups if g["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = sorted((g for g in groups if g["agent"] in WRITERS), key=lambda g: WRITERS.index(g["agent"]))
    active = state.get("active_flow") if state.get("active_flow") in WRITERS else None
    pending = AGENTS.get(state.get("pending_intent") or "")
    is_greeting = Intent.GREETING.value in state.get("intents", [])

    writer, deferred = None, None
    if writers:
        writer = writers[0]
        deferred = writers[1] if len(writers) > 1 else None
    elif active and (state.get("slots_updated") or slot_answer):
        writer = {"agent": active, "text": text}  # trả lời slot cho flow đang làm dở
    elif not groups and not is_greeting and pending in WRITERS:
        writer = {"agent": pending, "text": state.get("pending_text") or text}  # khách đồng ý ("Được")
    elif not groups and not is_greeting and active:
        writer = {"agent": active, "text": text}  # câu ngắn không rõ ý trong flow đang dở

    if not writer and not reads:  # flow/pending giữ nguyên trong checkpoint
        # Route to conversation agent for greeting/other
        update.update(_route_to_conversation(state, text))
        return update

    plan = [{"agent": g["agent"], "text": g["text"], "order": i} for i, g in enumerate(reads)]
    if writer:
        plan.append({"agent": writer["agent"], "text": writer["text"], "order": WRITER_ORDER})
        if active and writer["agent"] != active:
            update["active_flow"] = None  # chuyển sang flow ghi dữ liệu khác
    update.update(plan=plan, planned_agents=[p["agent"] for p in plan])

    if deferred:
        update.update(pending_intent=WRITER_INTENT[deferred["agent"]], pending_text=deferred["text"],
                      answers=[{"agent": "router", "order": NOTE_ORDER, "needs_human": False,
                                "text": DEFERRED_NOTE.format(label=WRITER_LABEL[deferred["agent"]])}])
    elif writers or (writer and writer["agent"] == pending):
        # Yêu cầu ghi dữ liệu mới thay thế việc đang chờ, hoặc vừa tiêu thụ nó.
        # Trả lời slot cho flow đang dở / hỏi thêm câu chỉ đọc thì giữ nguyên việc chờ.
        update.update(pending_intent=None, pending_text=None)
    return update


def dispatch(state: AgentState):
    """Fan-out: mỗi agent trong plan là một nhánh song song, nhận task_text riêng."""
    plan = state.get("plan", [])
    if not plan:
        return "compose"
    return [Send(p["agent"], {**state, "task_text": p["text"], "task_order": p["order"]}) for p in plan]
