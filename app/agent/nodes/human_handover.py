"""[TRACK C] Human Handover: tạo ticket + lead, chuyển mode HUMAN_PENDING, trả lời trung tính.
Nếu đang HUMAN_PENDING rồi: ghi thêm vào ticket đang mở, không tạo ticket mới."""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import ConversationMode
from app.services import get_services


def human_handover(state: AgentState) -> dict:
    reason = state.get("handover_reason") or "UNKNOWN"
    if state.get("mode") == ConversationMode.HUMAN_PENDING.value:
        get_services().handover.append(state["conversation_id"], reason, note=state["user_text"])
        reply = "Mình đã ghi chú thêm yêu cầu này để nhân viên xử lý luôn khi tiếp nhận, bạn vui lòng chờ chút nhé."
        return {"draft_reply": reply, "trace": step(state, "human_handover")}

    get_services().handover.create(state["conversation_id"], reason, summary=state["user_text"])
    reply = ("Mình đã chuyển yêu cầu của bạn cho nhân viên tư vấn, bạn vui lòng chờ trong giây lát. "
             "Trong lúc chờ, bạn vẫn có thể hỏi mình các thông tin khác.")
    if not state.get("slots", {}).get("phone"):
        reply += " Bạn cho mình xin số điện thoại để nhân viên liên hệ nhanh hơn nhé."
    if state.get("safety"):
        reply += f" Trường hợp khẩn cấp về kỹ thuật, bạn gọi hotline {settings.hotline}."
    # Timeout HUMAN_PENDING (không ai nhận sau HANDOVER_TIMEOUT_MINUTES -> nhắn hotline) xử lý ở conversation.py
    return {"draft_reply": reply, "set_mode": ConversationMode.HUMAN_PENDING.value,
            "trace": step(state, "human_handover")}
