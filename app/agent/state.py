import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    # input mỗi lượt
    session_id: str
    customer_id: int | None  # chỉ có khi SĐT đã xác minh OTP (được phép đọc dữ liệu cá nhân)
    user_text: str
    mode: str  # BOT (graph không chạy ở HUMAN_PENDING / HUMAN_ACTIVE)
    # preprocess (chỉ đọc, không side effect)
    intents: list[str]
    tasks: list[dict]  # [{intent, text}]: mỗi intent kèm phần câu của nó
    slots: dict[str, Any]  # được giữ qua các lượt nhờ checkpoint -> slot-filling nhiều lượt
    slots_updated: bool  # lượt này khách có cung cấp thêm slot không
    complexity: str  # LOW | HIGH
    active_flow: str | None  # luồng ghi dữ liệu đang làm dở (appointment | contract), giữ nhờ checkpoint
    # policy guard
    negotiation_active: bool  # sticky: đã bật thì giữ (đọc/ghi bảng chat_sessions)
    safety: bool
    route: str  # human | router
    # router (lập kế hoạch)
    plan: list[dict]  # [{agent, text, order}]: các agent chỉ đọc + tối đa 1 agent ghi dữ liệu
    planned_agents: list[str]
    pending_intent: str | None  # intent ghi dữ liệu bị hoãn sang lượt sau (vd CONTRACT + APPOINTMENT)
    pending_text: str | None  # phần câu gốc của pending_intent
    conversation_reason: str | None  # router → conversation: 'cancel' | 'clarify' | None (theo intent)
    # payload riêng của từng nhánh (router gửi qua Send)
    task_text: str
    task_order: int
    # agent (các nhánh song song chỉ ghi vào key có reducer, trừ agent ghi dữ liệu duy nhất)
    answers: Annotated[list[dict], operator.add]  # [{agent, order, text, needs_human, handover_reason}]
    allowed_amounts: Annotated[list[str], operator.add]  # số tiền được phép xuất hiện (lấy từ tool)
    # compose / guard
    draft_reply: str
    needs_human: bool
    handover_reason: str | None
    set_mode: str | None
    # output
    final_reply: str
    trace: Annotated[list[str], operator.add]  # đường đi qua các node -> dùng cho eval theo trajectory
