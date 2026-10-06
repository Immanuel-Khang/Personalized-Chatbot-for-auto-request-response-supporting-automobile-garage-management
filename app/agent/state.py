from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # input mỗi lượt
    conversation_id: int
    customer_id: int
    user_text: str
    mode: str  # BOT | HUMAN_PENDING (đọc từ bảng conversations)
    # preprocess (chỉ đọc, không side effect)
    intents: list[str]
    slots: dict[str, Any]  # được giữ qua các lượt nhờ checkpoint -> slot-filling nhiều lượt
    complexity: str  # LOW | HIGH
    active_flow: str | None  # luồng đang làm dở qua nhiều lượt (vd 'appointment'), giữ nhờ checkpoint
    # policy guard
    negotiation_active: bool  # sticky: đã bật thì giữ (đọc/ghi bảng conversations)
    safety: bool
    route: str  # human | knowledge | router
    # agent
    draft_reply: str
    allowed_amounts: list[str]  # các số tiền được phép xuất hiện (lấy từ tool)
    needs_human: bool
    handover_reason: str | None
    handoff_to: str | None  # agent chuyển tiếp sang agent khác trong cùng lượt (vd 'appointment')
    set_mode: str | None
    # output
    final_reply: str
    trace: list[str]  # đường đi qua các node -> dùng cho eval theo trajectory
