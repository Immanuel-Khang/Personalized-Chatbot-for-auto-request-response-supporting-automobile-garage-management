"""[TRACK B gọi, TRACK C cài BookingService] Appointment Agent: slot-filling nhiều lượt, bắt buộc có SĐT."""
from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, conversation_history, step
from app.agent.nodes.service_type import LABELS, clarify_question, resolve_service_type
from app.agent.state import AgentState
from app.contracts.schemas import ServiceType
from app.services import get_services

# Fallback templates when LLM not available
ASK_FALLBACK = {
    "service_type": "Bạn muốn đặt lịch bảo dưỡng, sửa chữa, lái thử hay xem xe tại showroom?",
    "when_text": "Bạn muốn đến vào ngày giờ nào?",
    "phone": "Bạn cho mình xin số điện thoại để xưởng xác nhận lịch nhé?",
}

APPOINTMENT_SYSTEM_PROMPT = """\
Bạn là trợ lý đặt lịch hẹn của xưởng Foton. Nhiệm vụ của bạn là thu thập đầy đủ thông tin để xác nhận lịch hẹn.

Thông tin cần thu thập:
1. service_type: Loại dịch vụ (bảo dưỡng | sửa chữa | lái thử | xem xe trực tiếp tại showroom)
2. when_text: Ngày giờ hẹn (VD: thứ 3 tuần sau, ngày mai sáng, 15/10)
3. phone: Số điện thoại liên hệ

Lịch sử hội thoại:
{history}

Thông tin đã có:
{filled_slots}

Thông tin còn thiếu:
{missing_slots}

Tin nhắn mới của khách: {user_text}

QUY TẮC:
- Luôn trả lời bằng tiếng Việt
- Hỏi một cách tự nhiên, không máy móc
- Nếu khách sửa thông tin đã có (VD: "không phải thứ 3, thứ 4 cơ"), chấp nhận sửa đổi
- Nếu còn thiếu thông tin, hỏi một cách thân thiện
- KHÔNG tự ý xác nhận lịch hẹn - chỉ hỏi thông tin còn thiếu
- Nếu khách cung cấp nhiều thông tin cùng lúc, ghi nhận tất cả
- KHÔNG đoán loại dịch vụ khi khách chưa nói rõ; hỏi lại
{style}"""


def _normalize_slots(slots: dict, service: ServiceType | None) -> dict:
    """Chuẩn hóa các tên slot khác nhau về một; service_type chỉ giữ giá trị enum hợp lệ."""
    normalized = dict(slots)
    if slots.get("appointment_when"):
        normalized["when_text"] = slots["appointment_when"]
    if slots.get("customer_phone"):
        normalized["phone"] = slots["customer_phone"]
    if service:
        normalized["service_type"] = service.value
    else:
        normalized.pop("service_type", None)  # chưa rõ / giá trị lạ → hỏi lại thay vì crash ở ServiceType(...)
    return normalized


def _missing_slots(slots: dict) -> list[str]:
    """Trả về danh sách slot còn thiếu."""
    required = ["service_type", "when_text", "phone"]
    return [s for s in required if not slots.get(s)]


def _generate_slot_question(state: AgentState, slots: dict, missing: list[str]) -> str:
    """Dùng LLM sinh câu hỏi tự nhiên cho slot còn thiếu."""
    # Lấy lịch sử hội thoại từ DB
    session_id = state.get("session_id", "")
    history = conversation_history(session_id, limit=6)

    # Format slot đã có và còn thiếu
    slot_names = {
        "service_type": "Loại dịch vụ",
        "when_text": "Ngày giờ hẹn",
        "phone": "Số điện thoại"
    }

    shown = {**slots, "service_type": LABELS[ServiceType(slots["service_type"])]} if slots.get("service_type") else slots
    filled_text = "\n".join(f"- {slot_names.get(k, k)}: {v}" for k, v in shown.items() if k in slot_names)
    missing_text = "\n".join(f"- {slot_names.get(m, m)}" for m in missing)

    if not filled_text:
        filled_text = "(chưa có thông tin nào)"
    if not missing_text:
        missing_text = "(đã đủ)"

    question = generate(
        APPOINTMENT_SYSTEM_PROMPT.format(
            history=history,
            filled_slots=filled_text,
            missing_slots=missing_text,
            user_text=state.get("user_text", ""),
            style=STYLE_RULES,
        ),
        "Hãy hỏi khách về thông tin còn thiếu một cách tự nhiên và thân thiện."
    )

    # Fallback nếu LLM không khả dụng
    if not question and missing:
        question = ASK_FALLBACK.get(missing[0], f"Bạn cho mình biết {slot_names.get(missing[0], missing[0])} nhé?")

    return question or "Bạn cần hỗ trợ gì thêm không?"


def appointment(state: AgentState) -> dict:
    raw_slots = state.get("slots", {})
    # Luật (service_type.py) quyết định trước, LLM/lượt trước chỉ bổ sung khi luật không kết luận được
    detection = resolve_service_type(state.get("task_text") or state.get("user_text", ""), raw_slots.get("service_type"))
    slots = _normalize_slots(raw_slots, detection.service)
    missing = _missing_slots(slots)

    if missing:
        if "service_type" in missing and detection.ambiguous:
            # Câu khách khớp nhiều loại dịch vụ → hỏi lại đúng các lựa chọn đó, không để LLM tự đoán
            question = clarify_question(detection.candidates)
        else:
            # Còn thiếu slot → hỏi bằng LLM
            question = _generate_slot_question(state, slots, missing)
        return {"answers": [answer(state, "appointment", question)],
                "active_flow": "appointment", "slots": slots,
                "trace": step(state, "appointment")}

    # Đủ slot → tạo lịch hẹn
    result = get_services().booking.create_appointment(
        state["session_id"], ServiceType(slots["service_type"]), slots["when_text"], slots["phone"]
    )

    if not result.ok:  # SĐT không hợp lệ → hỏi lại, giữ luồng
        slots.pop("phone", None)
        slots.pop("customer_phone", None)
        return {"answers": [answer(state, "appointment", f"{result.message} {ASK_FALLBACK['phone']}")],
                "active_flow": "appointment", "slots": slots, "trace": step(state, "appointment")}

    label = LABELS[ServiceType(slots["service_type"])]
    reply = f"Mình đã ghi nhận lịch hẹn {label} ({slots['when_text']}). {result.message}"
    return {"answers": [answer(state, "appointment", reply)], "slots": {}, "active_flow": None,
            "trace": step(state, "appointment")}
