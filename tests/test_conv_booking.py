"""Nhóm B (TC04-TC07): đặt lịch, slot nhiều lượt, hủy. Chạy bằng FakeLLM (không gọi LLM thật).

Luồng hiện tại của agent đặt lịch (khác thiết kế cũ mà file này từng mô tả):
  - Bắt buộc 4 thông tin: dịch vụ, ngày giờ, HỌ TÊN ĐẦY ĐỦ, số điện thoại; bot hỏi lần lượt phần còn thiếu.
  - Đủ thông tin thì bot ĐỀ XUẤT và hỏi xác nhận; chỉ GHI lịch (CONFIRMED) ở lượt khách xác nhận.
  - Ngày 'ngày mai' được quy đổi bằng code (date_extraction), lưu ở known_facts['appointment_date'].
FakeLLM chỉ quyết định 'LLM hiểu gì / gọi công cụ nào'; mọi phần còn lại là code thật. Việc bot có hiểu đúng câu tiếng Việt
hay không KHÔNG được kiểm ở đây (đó là L5).
"""
from unittest.mock import Mock

import pytest

from app.contracts.schemas import BookingResult
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.identity import normalize_phone
from app.services import get_services
from tests.conversation_helpers import (BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, Conversation, mentions, questions,
                                        unique_phone, unsupported_amounts)
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

TC04 = "Tôi muốn đặt lịch kiểm tra điều hòa ô tô vào ngày mai."
TC05 = "Tôi muốn đặt lịch thay dầu vào ngày mai lúc 10 giờ sáng."
TC06 = ["Tôi muốn đặt lịch bảo dưỡng xe vào ngày mai.", "10 giờ sáng nhé."]
TC07 = ["Tôi muốn đặt lịch bảo dưỡng vào ngày mai.", "Thôi, tôi không đặt nữa."]
NAME_ASK = "họ và tên"
SERVICE_ASK = "bảo dưỡng, sửa chữa"  # câu hỏi lại loại dịch vụ
READ_AGENTS = {"knowledge", "sales", "after_sales"}
BOOKED_MESSAGE = "đã đặt và xác nhận lịch hẹn"  # lời của BookingService khi ghi thành công
ACCEPT = task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER")


@pytest.fixture(autouse=True)
def clean_bookings():
    """Mỗi khung 'ngày mai 10h' chỉ chứa 2 lịch: dọn lịch/ticket/tin nhắn của test trước (không đụng catalog)."""
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def _workflow(state):
    workflow = state.get("active_workflow")
    return workflow.workflow_type if workflow else None


def _book_request(fake_llm, when_text: str, *, service="MAINTENANCE", evidence="bảo dưỡng", text: str | None = None,
                  with_identity: str | None = None) -> str:
    """Khai báo kịch bản: khách nói dịch vụ + ngày giờ (+ họ tên, SĐT nếu with_identity là số điện thoại)."""
    slots = [slot("service_type", service, evidence), slot("appointment_when", when_text, when_text)]
    if with_identity:
        slots += [slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", with_identity)]
    fake_llm.understands(text, task("APPOINTMENT", "book_appointment", evidence), slots=slots)
    return text


# ---- TC04: yêu cầu đặt lịch rõ ràng, có ngày ----
def test_tc04_explicit_request_enters_flow_and_keeps_date(client, fake_llm):
    conv = Conversation(client, "tc04")
    result = conv.say(_book_request(fake_llm, "ngày mai", service="REPAIR", evidence="kiểm tra điều hòa", text=TC04))
    state = conv.state
    assert "appointment" in result["trace"]
    assert _workflow(state) == "appointment" and "ngày mai" in _fact(state, "appointment_when")
    assert _fact(state, "appointment_date")["status"] == "RESOLVED"      # code đã quy đổi 'ngày mai' ra ngày cụ thể
    assert not mentions(result["reply"], DATE_ASK) and questions(result["reply"]) <= 1
    assert NAME_ASK in result["reply"].lower() and BOOKED_MESSAGE not in result["reply"].lower()
    assert conv.appointments() == []


def test_tc04_does_not_reask_service(client, fake_llm):
    conv = Conversation(client, "tc04-service")
    result = conv.say(_book_request(fake_llm, "ngày mai", service="REPAIR", evidence="kiểm tra điều hòa", text=TC04))
    assert _fact(conv.state, "service_type") and SERVICE_ASK not in result["reply"]


# ---- TC05: đủ dịch vụ + ngày giờ ngay lượt đầu ----
def test_tc05_full_request_unknown_customer_asks_for_missing_identity_only(client, fake_llm):
    conv = Conversation(client, "tc05")
    result = conv.say(_book_request(fake_llm, "ngày mai lúc 10 giờ sáng", evidence="thay dầu", text=TC05))
    assert _fact(conv.state, "service_type") == "MAINTENANCE" and "ngày mai" in _fact(conv.state, "appointment_when")
    assert NAME_ASK in result["reply"].lower() and not mentions(result["reply"], DATE_ASK)   # hỏi họ tên, không hỏi lại ngày giờ
    assert questions(result["reply"]) <= 1 and unsupported_amounts(result["reply"]) == []
    assert BOOKED_MESSAGE not in result["reply"].lower() and conv.appointments() == []


def test_tc05_keeps_exact_time(client, fake_llm):
    conv = Conversation(client, "tc05-time")
    conv.say(_book_request(fake_llm, "ngày mai lúc 10 giờ sáng", evidence="thay dầu", text=TC05))
    date_fact = _fact(conv.state, "appointment_date")
    assert "10" in _fact(conv.state, "appointment_when") and date_fact["time"] == "10:00"


def test_tc05_complete_details_propose_then_confirm_books_once(client, fake_llm):
    """Thay cho 'known phone books once' cũ (kỳ vọng ghi ngay, không xác nhận): luồng hiện tại là đề xuất rồi xác nhận."""
    conv, phone = Conversation(client, "tc05-confirm"), unique_phone()
    text = f"{TC05[:-1]}, tên Nguyễn Văn An, SĐT {phone}"
    first = conv.say(_book_request(fake_llm, "ngày mai lúc 10 giờ sáng", evidence="thay dầu", text=text,
                                   with_identity=phone))
    assert "xác nhận" in first["reply"] and conv.appointments() == []         # mới đề xuất, chưa ghi

    fake_llm.understands("Đúng rồi", ACCEPT, accepts_offer=True)
    second = conv.say("Đúng rồi")
    [row] = conv.appointments()
    assert row.status == "CONFIRMED" and row.service_type == "MAINTENANCE" and row.start_time.hour == 10
    assert row.contact_phone == normalize_phone(phone)
    assert BOOKED_MESSAGE in second["reply"].lower() and conv.customer_id() is not None


def test_tc05_failed_booking_is_not_reported_as_booked(client, fake_llm, monkeypatch):
    rejected = BookingResult(ok=False, status="REJECTED", message="Số điện thoại chưa hợp lệ.")
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(return_value=rejected))
    conv, phone = Conversation(client, "tc05-fail"), unique_phone()
    text = f"{TC05[:-1]}, tên Nguyễn Văn An, SĐT {phone}"
    conv.say(_book_request(fake_llm, "ngày mai lúc 10 giờ sáng", evidence="thay dầu", text=text, with_identity=phone))
    fake_llm.understands("Đúng rồi", ACCEPT, accepts_offer=True)
    result = conv.say("Đúng rồi")
    assert BOOKED_MESSAGE not in result["reply"].lower() and "đã đặt lịch" not in result["reply"].lower()
    assert _workflow(conv.state) == "appointment" and conv.appointments() == []


# ---- TC06: thu thập slot qua nhiều lượt ----
def _tc06_script(fake_llm):
    _book_request(fake_llm, "ngày mai", text=TC06[0])
    fake_llm.understands(TC06[1], task("APPOINTMENT", "book_appointment", "10 giờ sáng", "ANSWER_TO_QUESTION"),
                         slots=[slot("appointment_when", "ngày mai lúc 10 giờ sáng", "10 giờ sáng")])


def test_tc06_second_turn_keeps_booking_context(client, fake_llm):
    _tc06_script(fake_llm)
    conv = Conversation(client, "tc06")
    first = conv.say(TC06[0])
    assert _workflow(conv.state) == "appointment"
    assert questions(first["reply"]) <= 1 and not mentions(first["reply"], DATE_ASK)
    customer_before = conv.customer_id()
    second = conv.say(TC06[1])
    state = conv.state
    assert _workflow(state) == "appointment"
    assert _fact(state, "service_type") == "MAINTENANCE" and "ngày mai" in _fact(state, "appointment_when")
    assert not READ_AGENTS & set(second["trace"])
    assert conv.customer_id() == customer_before and conv.appointments() == []


def test_tc06_time_answer_fills_pending_slot(client, fake_llm):
    _tc06_script(fake_llm)
    conv = Conversation(client, "tc06-slot")
    conv.say(TC06[0])
    second = conv.say(TC06[1])
    assert "appointment" in second["trace"] and NOT_UNDERSTOOD not in second["reply"].lower()
    assert "10" in _fact(conv.state, "appointment_when") and _fact(conv.state, "appointment_date")["time"] == "10:00"
    assert not mentions(second["reply"], DATE_ASK)


# ---- TC07: hủy flow đặt lịch chưa hoàn tất ----
def test_tc07_cancel_incomplete_booking_clears_only_booking_state(client, fake_llm):
    _book_request(fake_llm, "ngày mai", text=TC07[0])
    fake_llm.understands(TC07[1], task("APPOINTMENT", "book_appointment", "không đặt nữa", "CANCEL_TASK"))
    fake_llm.understands("Được", task("OTHER", "general_conversation", "Được", "ACCEPT_OFFER"), accepts_offer=True)
    conv = Conversation(client, "tc07")
    customer = conv.link_customer(unique_phone())
    first = conv.say(TC07[0])
    assert _workflow(conv.state) == "appointment" and mentions(first["reply"], [NAME_ASK])
    cancelled = conv.say(TC07[1])
    state = conv.state
    assert "appointment" not in cancelled["trace"] and not mentions(cancelled["reply"], [NAME_ASK, "số điện thoại"])
    assert _workflow(state) is None
    assert all(item.status.value != "OPEN" for item in state.get("pending_interactions", []))
    assert not BOOKING_SLOTS & set(state.get("known_facts", {}))
    assert conv.appointments() == [] and conv.customer_id() == customer
    follow_up = conv.say("Được")
    assert "appointment" not in follow_up["trace"] and conv.appointments() == []


def test_tc07_cancelling_a_created_appointment_needs_confirmation_then_really_cancels(client, fake_llm):
    """Thay cho DEFECT-07 cũ ('bot báo đã dừng nhưng lịch vẫn còn'): nay có công cụ hủy, và hủy cũng đề xuất rồi xác nhận."""
    conv, phone = Conversation(client, "tc07-booked"), unique_phone()
    text = f"{TC05[:-1]}, tên Nguyễn Văn An, SĐT {phone}"
    conv.say(_book_request(fake_llm, "ngày mai lúc 10 giờ sáng", evidence="thay dầu", text=text, with_identity=phone))
    fake_llm.understands("Đúng rồi", ACCEPT, accepts_offer=True)
    conv.say("Đúng rồi")
    [row] = conv.appointments()
    assert row.status == "CONFIRMED"

    cancel_text = "Hủy lịch hẹn giúp mình"
    fake_llm.understands(cancel_text, task("APPOINTMENT", "cancel_appointment", "hủy lịch hẹn"))
    fake_llm.understands("Hủy đi", ACCEPT, accepts_offer=True)
    fake_llm.uses_tools("appointment", [
        [("get_appointment_details", {})],
        [("cancel_appointment", lambda results: {"appointment_id": results[-1]["appointments"][0]["id"]})]],
        when="hủy")
    first = conv.say(cancel_text)
    assert conv.appointments()[0].status == "CONFIRMED" and "xác nhận" in first["reply"].lower()  # mới đề xuất hủy
    second = conv.say("Hủy đi")
    assert conv.appointments()[0].status == "CANCELLED"
    assert "đã dừng" not in second["reply"].lower() and "hủy" in second["reply"].lower()
