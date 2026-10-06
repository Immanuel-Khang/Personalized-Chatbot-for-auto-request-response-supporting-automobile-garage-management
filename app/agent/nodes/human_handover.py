"""[TRACK C] Human Handover: tạo ticket + lead, chuyển mode HUMAN_PENDING, trả lời trung tính."""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.config import settings
from app.services import get_services


def human_handover(state: AgentState) -> dict:
    reason = state.get("handover_reason") or "UNKNOWN"
    get_services().handover.create(state["conversation_id"], reason, summary=state["user_text"])
    reply = "Mình đã chuyển yêu cầu của bạn cho nhân viên tư vấn, bạn vui lòng chờ trong giây lát."
    if not state.get("slots", {}).get("phone"):
        reply += " Bạn cho mình xin số điện thoại để nhân viên liên hệ nhanh hơn nhé."
    if state.get("safety"):
        reply += f" Trường hợp khẩn cấp về kỹ thuật, bạn gọi hotline {settings.hotline}."
    # TODO[HANDOVER]: timeout HUMAN_PENDING (không ai nhận sau X phút -> bot nhắn hotline)
    return {"draft_reply": reply, "set_mode": "HUMAN_PENDING", "trace": step(state, "human_handover")}
