from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # input mỗi lượt
    conversation_id: int
    customer_id: int
    user_text: str
    # preprocess (chỉ đọc, không side effect)
    intents: list[str]
    slots: dict[str, Any]  # được giữ qua các lượt nhờ checkpoint -> slot-filling nhiều lượt
    complexity: str  # LOW | HIGH
    active_flow: str | None  # luồng đang làm dở qua nhiều lượt (vd 'appointment'), giữ nhờ checkpoint
    # policy guard
    negotiation_active: bool  # sticky: đã bật thì giữ
    safety: bool
    route: str  # human | knowledge | router
    # discount workflow (ported from existing codebase)
    pending_discount_id: str | None  # ticket ID đang chờ duyệt
    discount_auto_approved: bool  # True nếu giảm ≤ ngưỡng, bot tự duyệt
    # agent
    draft_reply: str
    allowed_amounts: list[str]  # các số tiền được phép xuất hiện (lấy từ tool)
    needs_human: bool
    handover_reason: str | None
    set_mode: str | None
    # output
    final_reply: str
    trace: list[str]  # đường đi qua các node -> dùng cho eval theo trajectory
