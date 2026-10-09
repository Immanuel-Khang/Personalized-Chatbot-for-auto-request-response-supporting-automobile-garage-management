"""[TRACK B gọi, TRACK C cài BookingService] Appointment Agent: slot-filling nhiều lượt, bắt buộc có SĐT."""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import ServiceType
from app.services import get_services

ASK = {
    "service_type": "Bạn muốn đặt lịch bảo dưỡng, sửa chữa hay lái thử xe?",
    "when_text": "Bạn muốn đến vào ngày giờ nào?",
    "phone": "Bạn cho mình xin số điện thoại để xưởng xác nhận lịch nhé?",
}


def appointment(state: AgentState) -> dict:
    slots = dict(state.get("slots", {}))

    # appointment_when (từ LLM preprocess) và when_text (rule-based/manual) là cùng một slot
    # Chuẩn hóa về "when_text" để booking service dùng thống nhất
    if not slots.get("when_text") and slots.get("appointment_when"):
        slots["when_text"] = slots["appointment_when"]

    # Chuẩn hóa customer_phone -> phone
    if not slots.get("phone") and slots.get("customer_phone"):
        slots["phone"] = slots["customer_phone"]

    prefix = state.get("draft_reply", "") + "\n\n" if state.get("handoff_to") == "appointment" else ""
    for key in ("service_type", "when_text", "phone"):
        if not slots.get(key):
            return {"draft_reply": prefix + ASK[key], "active_flow": "appointment",
                    "slots": slots, "trace": step(state, "appointment")}

    result = get_services().booking.create_appointment(
        state["customer_id"], ServiceType(slots["service_type"]), slots["when_text"], slots["phone"]
    )
    reply = prefix + f"Mình đã ghi nhận lịch hẹn ({slots['when_text']}). {result.message}"
    return {"draft_reply": reply, "slots": {}, "active_flow": None, "trace": step(state, "appointment")}
