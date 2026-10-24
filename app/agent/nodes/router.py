"""[TRACK B] Intent Router = bộ lập kế hoạch, nơi duy nhất chọn agent.
- Agent chỉ đọc (sales, knowledge, after_sales): chạy song song, mỗi agent nhận đúng phần câu của mình.
- Agent ghi dữ liệu (contract, appointment): tối đa MỘT mỗi lượt, cái còn lại hoãn sang lượt sau.
- Không có agent nào: câu trả lời mặc định (chào / ngoài phạm vi / đã hủy)."""
from langgraph.types import Send

from app.agent.nodes.common import step
from app.agent.nodes.service_type import is_service_inquiry
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
BOOKING_SLOTS = {"service_type", "appointment_when", "when_text", "service_detail"}
# Hỏi thủ tục/giấy tờ là hỏi thông tin, chưa phải chốt hợp đồng → trả lời, không mở contract flow
CONTRACT_QUESTION = ("giấy tờ", "thủ tục", "hồ sơ", "cần chuẩn bị", "cần những gì", "điều kiện")
QUESTION = ["bao lâu", "bao nhiêu", "khi nào", "tại sao", "vì sao", "mốc", "chi phí", "cần làm gì"]
# Câu đồng ý ngắn: tiếp tục flow / việc đang chờ. Câu không rõ ý khác → hỏi lại, không đoán là trả lời slot.
AFFIRM = ("được", "ok", "oke", "okay", "ừ", "ừm", "vâng", "dạ", "có", "đồng ý", "tiếp tục", "đúng rồi", "uh")


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
        if _contract_inquiry(state, t):
            agent = "conversation"
        # 'Đặt lịch bảo dưỡng' / trả lời 'Bảo dưỡng' là loại dịch vụ, không phải câu hỏi bảo dưỡng
        if (t["intent"] == Intent.MAINTENANCE.value and booking
                and not any(k in t["text"].lower() for k in QUESTION)):
            slot_answer = True
            continue
        group = groups.setdefault(agent, {"agent": agent, "texts": []})
        if t["text"] not in group["texts"]:
            group["texts"].append(t["text"])
    return [{"agent": g["agent"], "text": ", ".join(g["texts"])} for g in groups.values()], slot_answer


def _contract_inquiry(state: AgentState, task: dict) -> bool:
    return (task["intent"] in (Intent.CONTRACT.value, Intent.SALES.value) and state.get("active_flow") != "contract"
            and any(k in task["text"].lower() for k in CONTRACT_QUESTION))


def _accepts_offer(state: AgentState, text: str) -> bool:
    """Lượt trước bot gợi ý đặt lịch: khách đồng ý hoặc trả lời ngày giờ → mới vào flow."""
    offer = state.get("offer") or {}
    return offer.get("intent") == Intent.APPOINTMENT.value and (_affirm(text) or bool(state.get("slots_updated")))


def _affirm(text: str) -> bool:
    return text.lower().strip().rstrip(".!?, ") in AFFIRM


def _route_to_conversation(state: AgentState, text: str, reason: str | None = None) -> dict:
    """Route to conversation agent. reason: 'cancel' | 'clarify' | None (chào / ngoài phạm vi theo intent)."""
    return {
        "plan": [{"agent": "conversation", "text": text, "order": 0}],
        "planned_agents": ["conversation"],
        "conversation_reason": reason,
    }


def router(state: AgentState) -> dict:
    # sales_question chỉ hiệu lực đúng 1 lượt: preprocess đã dùng nó, sales đặt lại nếu lại hỏi tiếp
    update: dict = {"trace": step(state, "router"), "sales_question": None}
    text = state.get("user_text", "")

    if state.get("safety"):  # cảnh báo an toàn luôn đi một mình, đọc cả câu
        update.update(plan=[{"agent": "knowledge", "text": text, "order": 0}], planned_agents=["knowledge"])
        return update

    offer = state.get("offer")
    update["offer"] = None  # gợi ý chỉ hiệu lực đúng lượt kế tiếp
    if _cancelled(state):
        update.update(pending_intent=None, pending_text=None, active_flow=None,
                      slots={k: v for k, v in state.get("slots", {}).items() if k not in BOOKING_SLOTS})
        # Route cancel to conversation agent instead of fixed reply
        update.update(_route_to_conversation(state, text, "cancel"))
        return update

    followup = state.get("followup") or {}
    declined = followup.get("reply") == "DECLINE"
    groups, slot_answer = _groups(state)
    if not groups and followup.get("clarify"):  # 'Có' khớp nhiều gợi ý → hỏi lại đúng các lựa chọn
        update.update(_route_to_conversation(state, text, "clarify_offer"))
        return update
    if not groups and declined and not state.get("active_flow"):
        # Từ chối gợi ý / việc đang chờ: ghi nhận, không mở việc chờ, không lặp lại gợi ý (offer/sales_question đã xóa)
        update.update(_route_to_conversation(state, text, "decline"), pending_intent=None, pending_text=None)
        return update
    reads = [g for g in groups if g["agent"] in READ_ONLY][:MAX_READ_AGENTS]
    writers = sorted((g for g in groups if g["agent"] in WRITERS), key=lambda g: WRITERS.index(g["agent"]))
    active = state.get("active_flow") if state.get("active_flow") in WRITERS else None
    pending = AGENTS.get(state.get("pending_intent") or "")
    is_greeting = Intent.GREETING.value in state.get("intents", [])

    writer, deferred = None, None
    if writers:
        writer = writers[0]
        deferred = writers[1] if len(writers) > 1 else None
    elif active and (state.get("slots_updated") or slot_answer
                     or (active == "appointment" and state.get("phone_rejected"))):
        writer = {"agent": active, "text": text}  # trả lời slot cho flow đang làm dở (SĐT sai → flow hỏi lại SĐT)
    elif not groups and not is_greeting and not active and _accepts_offer(state, text):
        writer = {"agent": "appointment", "text": offer.get("text") or text}  # khách nhận lời gợi ý đặt lịch
    elif not groups and not is_greeting and pending in WRITERS:
        writer = {"agent": pending, "text": state.get("pending_text") or text}  # khách đồng ý ("Được")
    elif not groups and not is_greeting and active and _affirm(text):
        writer = {"agent": active, "text": text}  # "Được" / "Ok" trong flow đang dở → hỏi tiếp slot còn thiếu

    if (offer or {}).get("intent") == Intent.APPOINTMENT.value and not active \
            and not (writer and writer["agent"] == "appointment"):
        # Không nhận lời gợi ý: bỏ slot đặt lịch preprocess vừa lấy theo ngữ cảnh gợi ý
        update["slots"] = {k: v for k, v in state.get("slots", {}).items() if k not in BOOKING_SLOTS}

    if not writer and not reads:  # flow/pending giữ nguyên trong checkpoint
        if not is_greeting and not active and not pending and is_service_inquiry(text):
            # Hỏi dịch vụ: trả lời trước, gợi ý mang xe qua kiểm tra; KHÔNG tự mở flow đặt lịch
            update.update(_route_to_conversation(state, text, "service_inquiry"),
                          offer={"intent": Intent.APPOINTMENT.value, "text": text})
            return update
        # Không hiểu khách muốn gì trong lúc có flow/việc chờ → hỏi lại, không tự đoán là trả lời slot
        unclear = not is_greeting and bool(active or pending)
        update.update(_route_to_conversation(state, text, "clarify" if unclear else None))
        return update

    plan = [{"agent": g["agent"], "text": g["text"], "order": i} for i, g in enumerate(reads)]
    if writer:
        plan.append({"agent": writer["agent"], "text": writer["text"], "order": WRITER_ORDER})
        if active and writer["agent"] != active:
            update["active_flow"] = None  # chuyển sang flow ghi dữ liệu khác
    update.update(plan=plan, planned_agents=[p["agent"] for p in plan])
    if any(_contract_inquiry(state, t) for t in state.get("tasks", [])):
        update["conversation_reason"] = "contract_inquiry"

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
