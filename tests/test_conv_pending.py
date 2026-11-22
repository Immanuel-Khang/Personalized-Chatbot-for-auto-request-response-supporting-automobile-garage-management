"""Short replies and profile persistence under canonical conversation state."""
import uuid

from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal
from app.identity import get_or_create_session, normalize_phone
from tests.conversation_helpers import PHONE_ASK, Conversation, mentions, unique_phone
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

SHOWROOM = "Tôi muốn đặt lịch xem xe tại showroom"
WHEN = "Tôi muốn đến vào thứ 7 tuần này lúc 8 giờ sáng, tên Nguyễn Văn An"
ELEVEN_DIGITS = "09123456789"


def _value(state: dict, key: str):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def test_invalid_phone_reasks_phone_and_keeps_facts(client, fake_llm):
    """Số 11 chữ số bị từ chối: không lưu vào fact/hồ sơ, giữ nguyên các thông tin khác, hỏi lại SĐT.
    Luồng hiện tại: sau khi có SĐT hợp lệ bot ĐỀ XUẤT rồi mới ghi ở lượt xác nhận."""
    phone = unique_phone()
    answer = "ANSWER_TO_QUESTION"
    fake_llm.understands(SHOWROOM, task("APPOINTMENT", "book_appointment", "xem xe showroom"),
                         slots=[slot("service_type", "SHOWROOM_VISIT", "showroom")])
    fake_llm.understands(WHEN, task("APPOINTMENT", "book_appointment", "thứ 7", answer), slots=[
        slot("appointment_when", "thứ 7 tuần này lúc 8 giờ sáng", "thứ 7 tuần này lúc 8 giờ sáng"),
        slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An")])
    fake_llm.understands(ELEVEN_DIGITS, task("APPOINTMENT", "book_appointment", ELEVEN_DIGITS, answer),
                         slots=[slot("customer_phone", ELEVEN_DIGITS, ELEVEN_DIGITS)])
    fake_llm.understands(phone, task("APPOINTMENT", "book_appointment", phone, answer),
                         slots=[slot("customer_phone", phone, phone)])
    fake_llm.understands("Đúng rồi", task("APPOINTMENT", "book_appointment", "Đúng rồi", "ACCEPT_OFFER"),
                         accepts_offer=True)
    conv = Conversation(client, "bad-phone")
    conv.say(SHOWROOM)
    conv.say(WHEN)
    result = conv.say(ELEVEN_DIGITS)
    state = conv.state
    assert "appointment" in result["trace"] and mentions(result["reply"], PHONE_ASK)
    assert state["active_workflow"].workflow_type == "appointment"
    assert _value(state, "service_type") == "SHOWROOM_VISIT" and _value(state, "appointment_when")
    assert _value(state, "customer_phone") is None
    assert conv.appointments() == [] and conv.customer_id() is None

    proposal = conv.say(phone)
    assert "xác nhận" in proposal["reply"].lower() and conv.appointments() == []
    booked = conv.say("Đúng rồi")
    rows = conv.appointments()
    assert "appointment" in booked["trace"] and len(rows) == 1
    assert rows[0].service_type == "SHOWROOM_VISIT" and rows[0].contact_phone == normalize_phone(phone)
    assert conv.state["active_workflow"].status.value == "WAITING_FOR_APPROVAL"   # chủ đích sản phẩm


def _session_customer(session_id: str) -> Customer | None:
    with SessionLocal() as db:
        session = db.get(ChatSession, session_id)
        return db.get(Customer, session.customer_id) if session and session.customer_id else None


def test_profile_save_normalizes_and_rejects_invalid_phone():
    """Profile persistence lives in app/conversation.py (_save_phone -> attach_phone)."""
    from app.conversation import _save_phone
    bad, good = f"profile-bad-{uuid.uuid4().hex[:8]}", f"profile-ok-{uuid.uuid4().hex[:8]}"
    phone = unique_phone()
    for sid, raw in ((bad, ELEVEN_DIGITS), (good, phone)):
        with SessionLocal() as db:
            session = get_or_create_session(db, sid)
            _save_phone(db, session, raw)
            db.commit()
    assert _session_customer(bad) is None
    customer = _session_customer(good)
    assert customer and customer.phone_number == normalize_phone(phone)
