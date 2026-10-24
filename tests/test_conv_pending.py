"""Câu trả lời ngắn / câu trả lời slot theo đúng câu bot đang chờ (Part 1 của đợt chỉnh kiến trúc).
Chạy trên garage_test, rule-based; diễn giải LLM được mock qua preprocess.generate_structured."""
import uuid
from unittest.mock import Mock

from app.agent.nodes.appointment import PHONE_INVALID
from app.agent.nodes.preprocess import _llm_preprocess, _save_profile_to_db, merge_when
from app.agent.nodes.router import router
from app.contracts.schemas import CustomerSlots, FollowUp, BotReply, Intent, IntentAndSlotExtraction, IntentTask
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal
from app.identity import get_or_create_session, normalize_phone
from tests.conversation_helpers import Conversation, unique_phone

SHOWROOM = "Tôi muốn đặt lịch xem xe tại showroom"
WHEN = "Tôi muốn đến vào thứ 7 tuần này lúc 8 giờ sáng"
ELEVEN_DIGITS = "09123456789"


def test_bare_yes_after_statement_does_not_open_write_flow(monkeypatch):
    """Lượt bot trước không hỏi gì: LLM gắn CONTRACT/APPOINTMENT + ACCEPT cho 'Có' cũng không mở flow ghi dữ liệu."""
    llm = IntentAndSlotExtraction(
        intents=[Intent.CONTRACT, Intent.APPOINTMENT],
        tasks=[IntentTask(intent=Intent.CONTRACT, text="Có"), IntentTask(intent=Intent.APPOINTMENT, text="Có")],
        followup=FollowUp(reply_to_bot=BotReply.ACCEPT))
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=llm))
    state = {"user_text": "Có", "slots": {}, "history": [
        {"role": "user", "content": "Giá xe FUSO Canter FI170L bao nhiêu"},
        {"role": "ai", "content": "Giá xe FUSO Canter FI170L là 845.000.000 đồng (cập nhật 2026-10-18)."}]}
    output = _llm_preprocess(state)
    assert output["intents"] == ["OTHER"] and output["tasks"] == []
    assert output["followup"].get("reply") is None  # không có câu hỏi thì không có 'đồng ý'
    update = router({**state, **output})
    assert update["planned_agents"] == ["conversation"] and update.get("active_flow") is None


def test_invalid_phone_reasks_phone_and_keeps_slots(client):
    conv = Conversation(client, "bad-phone")
    conv.say(SHOWROOM)
    conv.say(WHEN)
    result = conv.say(ELEVEN_DIGITS)
    state = conv.state
    assert "appointment" in result["trace"] and PHONE_INVALID in result["reply"]
    assert state["active_flow"] == "appointment"
    assert state["slots"]["service_type"] == "SHOWROOM_VISIT" and state["slots"]["when_text"]
    assert "customer_phone" not in state["slots"] and "phone" not in state["slots"]
    assert conv.appointments() == [] and conv.customer_id() is None  # SĐT sai không vào hồ sơ khách

    phone = unique_phone()  # khách sửa lại SĐT: đặt lịch tiếp, không mất loại dịch vụ / ngày giờ
    booked = conv.say(phone)
    rows = conv.appointments()
    assert "appointment" in booked["trace"] and len(rows) == 1
    assert rows[0].service_type == "SHOWROOM_VISIT" and rows[0].contact_phone == normalize_phone(phone)
    assert conv.state.get("active_flow") is None


def test_llm_time_answer_keeps_previous_day(monkeypatch):
    llm = IntentAndSlotExtraction(intents=[Intent.APPOINTMENT],
                                  extracted_slots=CustomerSlots(appointment_when="8 giờ sáng"))
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=llm))
    state = {"user_text": "8 giờ sáng", "active_flow": "appointment",
             "slots": {"service_type": "TEST_DRIVE", "appointment_when": "Thứ bảy"}}
    assert _llm_preprocess(state)["slots"]["appointment_when"] == "Thứ bảy 8 giờ sáng"


def test_merge_when():
    assert merge_when(None, "8 giờ sáng") == "8 giờ sáng"
    assert merge_when("Thứ bảy", "8 giờ sáng") == "Thứ bảy 8 giờ sáng"
    assert merge_when("thứ 7 9 giờ", "8 giờ sáng") == "thứ 7 8 giờ sáng"  # đổi giờ, giữ ngày
    assert merge_when("Thứ bảy", "Chủ nhật 10 giờ") == "Chủ nhật 10 giờ"  # khách đổi ngày


def _session_customer(session_id: str) -> Customer | None:
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        return db.get(Customer, session.customer_id) if session and session.customer_id else None


def test_profile_save_normalizes_and_rejects_invalid_phone():
    bad, good = f"profile-bad-{uuid.uuid4().hex[:8]}", f"profile-ok-{uuid.uuid4().hex[:8]}"
    for sid in (bad, good):
        with SessionLocal() as db:
            get_or_create_session(db, sid)
            db.commit()
    _save_profile_to_db(bad, None, ELEVEN_DIGITS)
    assert _session_customer(bad) is None

    phone = unique_phone()
    _save_profile_to_db(good, None, phone)
    customer = _session_customer(good)
    assert customer and customer.phone_number == normalize_phone(phone)  # cùng định dạng +84 với BookingService
