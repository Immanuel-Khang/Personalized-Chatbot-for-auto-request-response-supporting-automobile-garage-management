"""Nhóm B (TC04-TC07): đặt lịch, slot nhiều lượt, hủy, chỉ báo đã ghi nhận khi BookingService thành công.
Thiết kế hiện tại: slot bắt buộc = service_type, when_text (văn bản tự do, không quy đổi ngày -> không phụ thuộc
đồng hồ), phone. Lịch tạo ra luôn PENDING_APPROVAL; 'ngày mai' đã đủ when_text nên bot hỏi SĐT, không hỏi giờ."""
from unittest.mock import Mock

from app.contracts.schemas import BookingResult
from app.identity import normalize_phone
from app.services import get_services
from tests.conversation_helpers import (BOOKED, BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation,
                                        defect, mentions, questions, unique_phone, unsupported_amounts)

TC04 = "Tôi muốn đặt lịch kiểm tra điều hòa ô tô vào ngày mai."
TC05 = "Tôi muốn đặt lịch thay dầu vào ngày mai lúc 10 giờ sáng."
TC06 = ["Tôi muốn đặt lịch bảo dưỡng xe vào ngày mai.", "10 giờ sáng nhé."]
TC07 = ["Tôi muốn đặt lịch bảo dưỡng vào ngày mai.", "Thôi, tôi không đặt nữa."]
SERVICE_ASK = "bảo dưỡng, sửa chữa"  # câu hỏi lại loại dịch vụ (ASK_FALLBACK / clarify_question)
READ_AGENTS = {"knowledge", "sales", "after_sales"}


# ---- TC04: yêu cầu đặt lịch rõ ràng, có ngày ----
def test_tc04_explicit_request_enters_flow_and_keeps_date(client):
    conv = Conversation(client, "tc04")
    result = conv.say(TC04)
    state = conv.state
    assert "APPOINTMENT" in state["intents"] and "appointment" in result["trace"]
    assert state["active_flow"] == "appointment" and state["slots"]["when_text"] == "ngày mai"
    assert not mentions(result["reply"], DATE_ASK) and questions(result["reply"]) <= 1  # ngày đã có
    assert BOOKED not in result["reply"] and conv.appointments() == []


def test_tc04_does_not_reask_service(client):
    conv = Conversation(client, "tc04-service")
    result = conv.say(TC04)
    assert conv.state["slots"].get("service_type") and SERVICE_ASK not in result["reply"]


# ---- TC05: đủ dịch vụ + ngày giờ ngay lượt đầu ----
def test_tc05_full_request_unknown_customer_asks_only_phone(client):
    conv = Conversation(client, "tc05")
    result = conv.say(TC05)
    slots = conv.state["slots"]
    assert slots["service_type"] == "MAINTENANCE" and "ngày mai" in slots["when_text"]
    assert mentions(result["reply"], PHONE_ASK) and not mentions(result["reply"], DATE_ASK)
    assert questions(result["reply"]) <= 1 and unsupported_amounts(result["reply"]) == []
    assert BOOKED not in result["reply"] and conv.appointments() == []


def test_tc05_keeps_exact_time(client):
    conv = Conversation(client, "tc05-time")
    conv.say(TC05)
    assert "10" in conv.state["slots"]["when_text"]


def test_tc05_known_phone_books_once_pending_approval(client):
    conv = Conversation(client, "tc05-known")
    phone = unique_phone()
    conv.say(f"Số điện thoại của mình là {phone}")
    result = conv.say(TC05)
    assert not mentions(result["reply"], PHONE_ASK + DATE_ASK)  # không hỏi lại thông tin đã có
    [row] = conv.appointments()
    assert row.status == "PENDING_APPROVAL" and row.service_type == "MAINTENANCE"
    assert row.contact_phone == normalize_phone(phone)
    # Lời xác nhận khớp trạng thái DB: đã ghi nhận nhưng còn chờ nhân viên duyệt
    assert BOOKED in result["reply"] and "chờ nhân viên xác nhận" in result["reply"]
    assert conv.state["active_flow"] is None and conv.customer_id() is not None


def test_tc05_failed_booking_is_not_reported_as_booked(client, monkeypatch):
    rejected = BookingResult(ok=False, status="REJECTED", message="Số điện thoại chưa hợp lệ.")
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(return_value=rejected))
    conv = Conversation(client, "tc05-fail")
    conv.say(f"Số điện thoại của mình là {unique_phone()}")
    result = conv.say(TC05)
    assert BOOKED not in result["reply"] and conv.state["active_flow"] == "appointment"
    assert conv.appointments() == []


# ---- TC06: thu thập slot qua nhiều lượt ----
def test_tc06_second_turn_keeps_booking_context(client):
    conv = Conversation(client, "tc06")
    first = conv.say(TC06[0])
    assert conv.state["active_flow"] == "appointment"
    assert questions(first["reply"]) <= 1 and not mentions(first["reply"], DATE_ASK)
    customer_before = conv.customer_id()
    second = conv.say(TC06[1])
    state = conv.state
    assert state["active_flow"] == "appointment"
    assert state["slots"]["service_type"] == "MAINTENANCE" and "ngày mai" in state["slots"]["when_text"]
    assert not READ_AGENTS & set(second["trace"])  # không tạo task bảo dưỡng/kiến thức không liên quan
    assert conv.customer_id() == customer_before and conv.appointments() == []


def test_tc06_time_answer_fills_pending_slot(client):
    conv = Conversation(client, "tc06-slot")
    conv.say(TC06[0])
    second = conv.say(TC06[1])
    assert "appointment" in second["trace"] and NOT_UNDERSTOOD not in second["reply"].lower()
    assert "10" in conv.state["slots"]["when_text"] and not mentions(second["reply"], DATE_ASK)


# ---- TC07: hủy flow đặt lịch chưa hoàn tất ----
def test_tc07_cancel_incomplete_booking_clears_only_booking_state(client):
    conv = Conversation(client, "tc07")
    customer = conv.link_customer(unique_phone())
    first = conv.say(TC07[0])
    assert conv.state["active_flow"] == "appointment" and mentions(first["reply"], DATE_ASK + PHONE_ASK)
    cancelled = conv.say(TC07[1])
    state = conv.state
    assert "appointment" not in cancelled["trace"] and not mentions(cancelled["reply"], DATE_ASK + PHONE_ASK)
    assert state["active_flow"] is None and not state.get("pending_intent")
    assert not BOOKING_SLOTS & set(state["slots"])
    assert conv.appointments() == [] and conv.customer_id() == customer  # danh tính không bị xóa
    follow_up = conv.say("Được")  # không bị hiểu là tiếp tục flow đã hủy
    assert "appointment" not in follow_up["trace"] and conv.appointments() == []


@defect("DEFECT-07", "chưa có luồng hủy lịch đã tạo: bot báo 'đã dừng' nhưng lịch vẫn PENDING_APPROVAL")
def test_tc07_cancelling_created_appointment_is_not_falsely_confirmed(client):
    conv = Conversation(client, "tc07-booked")
    conv.say(TC07[0])
    conv.say(unique_phone())
    [row] = conv.appointments()
    assert row.status == "PENDING_APPROVAL"
    reply = conv.say("Hủy lịch hẹn giúp mình")["reply"].lower()
    [row] = conv.appointments()
    claims_cancelled = any(m in reply for m in ("đã dừng", "đã hủy", "đã huỷ"))
    assert row.status != "PENDING_APPROVAL" or not claims_cancelled
