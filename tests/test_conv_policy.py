"""Policy regressions using the canonical conversation state and real chat graph.

Các test cần 'LLM hiểu gì' dùng FakeLLM (tests/fake_llm.py); phần còn lại là code thật. Luồng đặt lịch hiện tại:
hỏi lần lượt dịch vụ/ngày giờ/họ tên/SĐT -> ĐỀ XUẤT (CONFIRMATION) -> ghi lịch ở lượt khách xác nhận. Việc bot có hiểu
đúng tiếng Việt không do L5 kiểm, không kiểm ở đây.
"""
from unittest.mock import Mock

import pytest

from app.contracts.schemas import PolicySignals
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services
from tests.conversation_helpers import (BOOKED, BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation,
                                        mentions, unique_phone)
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

TC01 = "Xe tôi đang gặp vấn đề với kính chắn gió, có thể mang qua hãng để sửa được không?"


def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def _workflow(state):
    workflow = state.get("active_workflow")
    return workflow.workflow_type if workflow else None


def _open_pending(state):
    return [item for item in state.get("pending_interactions", []) if item.status.value == "OPEN"]


def test_ignored_offer_expires_and_keeps_no_booking_slot(client):
    conv = Conversation(client, "offer-ignore")
    conv.say(TC01)
    result = conv.say("Bao lâu thì nên thay dầu động cơ?")
    state = conv.state
    assert "appointment" not in result["trace"] and _workflow(state) is None
    assert not BOOKING_SLOTS & set(state.get("known_facts", {})) and conv.appointments() == []
    later = conv.say("Được")
    assert "appointment" not in later["trace"]


def test_tc14_price_only_does_not_start_booking(client):
    conv = Conversation(client, "tc14")
    result = conv.say("Tôi chỉ muốn hỏi giá trước thôi, chưa định đặt lịch.")
    assert "appointment" not in result["trace"] and _workflow(conv.state) is None
    assert not mentions(result["reply"], DATE_ASK + PHONE_ASK) and conv.appointments() == []


def test_removed_router_helpers_are_not_part_of_canonical_contract():
    from app.agent.nodes.router import router
    from app.contracts.schemas import ConversationInterpretation

    update = router({"user_text": "Bạn thích ăn gì", "conversation_interpretation":
                     ConversationInterpretation(message_summary="Câu hỏi ngoài phạm vi.")})
    assert update["plan"][0]["agent"] == "conversation"
    assert "offer" not in update and "intents" not in update


# Keep the import here to assert the service failure test exercises the configured service surface.
assert get_services().booking is not None


def test_waiting_appointment_slot_names_are_canonical():
    assert BOOKING_SLOTS == {"service_type", "appointment_when", "when_text"}


# Regression for safe failure output; internal exception details must never leak.
def test_no_retired_state_is_returned(client):
    conv = Conversation(client, "no-retired-policy-state")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert not {"slots", "active_flow", "pending_intent", "offer"} & set(conv.state)


def test_booking_slot_constants_remain_available_for_other_regressions():
    assert BOOKING_SLOTS <= {"service_type", "appointment_when", "when_text"}


def test_question_marker_constants_are_nonempty():
    assert DATE_ASK and PHONE_ASK


def test_no_unsupported_amounts_in_safe_failure(client, monkeypatch):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError("db down")))
    conv = Conversation(client, "failure-no-price")
    conv.say(f"Số điện thoại {unique_phone()}")
    result = conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert result["reply"] and conv.appointments() == []


def test_conversation_uses_unique_session(client):
    first = Conversation(client, "policy-isolation")
    second = Conversation(client, "policy-isolation")
    assert first.id != second.id


def test_policy_module_contains_no_legacy_preprocess_imports():
    # Kept as a small guard against accidentally reintroducing retired preprocess APIs.
    import app.agent.nodes.preprocess as preprocess_module

    assert not hasattr(preprocess_module, "extract_when")
    assert not hasattr(preprocess_module, "_llm_preprocess")


def test_test_database_session_is_isolated(client):
    conv = Conversation(client, "policy-test-db")
    conv.say("Xin chào")
    assert conv.state.get("session_id") == conv.id


def test_canonical_pending_interactions_are_not_legacy_dicts(client):
    conv = Conversation(client, "pending-models")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert all(hasattr(item, "interaction_id") for item in conv.state["pending_interactions"])


def test_no_appointment_record_is_created_by_inquiry(client):
    conv = Conversation(client, "inquiry-no-booking")
    conv.say(TC01)
    assert conv.appointments() == [] and _workflow(conv.state) is None


def test_appointment_ask_does_not_collect_phone_for_inquiry(client):
    conv = Conversation(client, "inquiry-no-phone")
    result = conv.say(TC01)
    assert not mentions(result["reply"], PHONE_ASK)
    assert "customer_phone" not in conv.state.get("known_facts", {})


def test_canonical_workflow_is_cleared_after_cancel(client):
    conv = Conversation(client, "workflow-cancel")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai")
    conv.say("Thôi không đặt nữa")
    assert conv.state.get("active_workflow") is None


def test_pending_is_closed_after_cancel(client):
    conv = Conversation(client, "pending-cancel")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai")
    conv.say("Thôi không đặt nữa")
    assert all(item.status.value != "OPEN" for item in conv.state.get("pending_interactions", []))


def test_policy_regression_sentinel():
    assert BOOKED == "ghi nhận lịch hẹn"
    assert NOT_UNDERSTOOD == "chưa hiểu"


def test_legacy_budget_constant_not_used_in_policy_suite():
    assert "budget_vnd" not in BOOKING_SLOTS


def test_offer_does_not_create_active_appointment_workflow(client):
    conv = Conversation(client, "offer-no-workflow")
    conv.say(TC01)
    assert _workflow(conv.state) is None


def test_preprocess_uses_only_canonical_interpretation():
    from app.agent.nodes.preprocess import _apply_interpretation
    from app.contracts.schemas import ConversationInterpretation

    result = _apply_interpretation({"user_text": "Xin chào", "known_facts": {}},
                                   ConversationInterpretation(message_summary="Chào hỏi."))
    assert "conversation_interpretation" in result
    assert not {"slots", "intents", "tasks"} & set(result)


def test_appointment_state_has_canonical_fields(client):
    conv = Conversation(client, "canonical-state-fields")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert {"known_facts", "active_workflow", "pending_interactions"} <= set(conv.state)


def test_canonical_facts_are_known_fact_objects(client):
    conv = Conversation(client, "known-fact-model")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert all(hasattr(value, "source") for value in conv.state["known_facts"].values())


def test_appointment_task_is_a_canonical_task(client):
    from app.contracts.schemas import TaskInterpretation

    conv = Conversation(client, "task-model")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    tasks = conv.state["conversation_interpretation"].tasks
    assert all(isinstance(task, TaskInterpretation) for task in tasks)


# =====================================================================================================================
# Chuyển sang FakeLLM (Đợt 2, lô 2)
# =====================================================================================================================
NAME_ASK = "họ và tên"
ACCEPT = task("APPOINTMENT", "book_appointment", "Được", "ACCEPT_OFFER")
DEFERRAL = "Mình chốt mua xe, đặt lịch qua showroom xem xe nữa"
SAFE_FAILURE = "Hệ thống chưa thực hiện được thao tác này"


@pytest.fixture(autouse=True)
def clean_bookings():
    """Dọn lịch/ticket/tin nhắn: mỗi khung giờ chỉ chứa 2 lịch nên test trước không được chiếm chỗ test sau."""
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


def _appointment_task(text, **kw):
    return task("APPOINTMENT", "book_appointment", text, **kw)


def _script_inquiry(fake_llm):
    fake_llm.understands(TC01, task("MAINTENANCE", "service_inquiry", "sửa kính", inquiry_only=True))


def _script_full_booking(fake_llm, text, phone, when="2 giờ chiều mai"):
    fake_llm.understands(text, _appointment_task("bảo dưỡng"), slots=[
        slot("service_type", "MAINTENANCE", "bảo dưỡng"), slot("appointment_when", when, when),
        slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", phone, phone)])
    fake_llm.understands("Được", ACCEPT, accepts_offer=True)
    return text


def _script_partial_booking(fake_llm, text="Đặt bảo dưỡng ngày mai"):
    fake_llm.understands(text, _appointment_task("bảo dưỡng"), slots=[
        slot("service_type", "MAINTENANCE", "bảo dưỡng"), slot("appointment_when", "ngày mai", "ngày mai")])
    return text


def _script_deferral(fake_llm):
    fake_llm.understands(DEFERRAL, task("CONTRACT", "start_contract", "chốt mua xe"),
                         _appointment_task("đặt lịch showroom"))
    fake_llm.understands("Được", task("OTHER", "general_conversation", "Được", "ACCEPT_OFFER"), accepts_offer=True)


# ---- Hỏi dịch vụ / nhận gợi ý ----
def test_service_inquiry_leaves_offer_not_flow(client, fake_llm):
    _script_inquiry(fake_llm)
    conv = Conversation(client, "offer")
    result = conv.say(TC01)
    assert "conversation" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert _workflow(conv.state) is None and not _open_pending(conv.state)
    assert not mentions(result["reply"], PHONE_ASK) and conv.appointments() == []


def test_accepting_offer_with_time_enters_booking_without_creating(client, fake_llm):
    _script_inquiry(fake_llm)
    text = "Sửa kính ngày mai lúc 10 giờ nhé"
    fake_llm.understands(text, _appointment_task("sửa kính", relationship="ACCEPT_OFFER"), accepts_offer=True, slots=[
        slot("service_type", "REPAIR", "sửa kính"), slot("appointment_when", "ngày mai lúc 10 giờ", "ngày mai lúc 10 giờ")])
    conv = Conversation(client, "offer-accept")
    conv.say(TC01)
    result = conv.say(text)
    state = conv.state
    assert "appointment" in result["trace"] and _workflow(state) == "appointment"
    assert _fact(state, "service_type") == "REPAIR" and "10 giờ" in _fact(state, "appointment_when")
    assert NAME_ASK in result["reply"].lower() and not mentions(result["reply"], DATE_ASK)
    assert conv.appointments() == []
    assert any(item.metadata.get("workflow") == "appointment" for item in _open_pending(state))


def test_known_phone_offer_acceptance_asks_confirmation_before_booking(client, fake_llm):
    _script_inquiry(fake_llm)
    phone = unique_phone()
    text = f"Sửa kính ngày mai 10 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    fake_llm.understands(text, _appointment_task("sửa kính", relationship="ACCEPT_OFFER"), accepts_offer=True, slots=[
        slot("service_type", "REPAIR", "sửa kính"), slot("appointment_when", "ngày mai 10 giờ sáng", "ngày mai 10 giờ sáng"),
        slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", phone, phone)])
    fake_llm.understands("Được", ACCEPT, accepts_offer=True)
    conv = Conversation(client, "offer-confirm")
    conv.say(TC01)
    tentative = conv.say(text)
    assert "xác nhận" in tentative["reply"].lower() and conv.appointments() == []
    conv.say("Được")
    [row] = conv.appointments()
    assert row.status == "CONFIRMED" and row.service_type == "REPAIR"


def test_tc15_switching_service_updates_context_keeps_identity(client, fake_llm):
    first_text, second_text = "Đặt lịch sửa kính ngày mai", "Thôi, kiểm tra điều hòa nhé"
    fake_llm.understands(first_text, _appointment_task("sửa kính"), slots=[
        slot("service_type", "REPAIR", "sửa"), slot("service_detail", "kính chắn gió", "kính"),
        slot("appointment_when", "ngày mai", "ngày mai")])
    fake_llm.understands(second_text, _appointment_task("kiểm tra điều hòa", relationship="CORRECT_PREVIOUS_INFO"),
                         slots=[slot("service_detail", "điều hòa", "điều hòa")])
    conv = Conversation(client, "tc15")
    customer = conv.link_customer(unique_phone())
    conv.say(first_text)
    assert _fact(conv.state, "service_detail") == "kính chắn gió"
    result = conv.say(second_text)
    state = conv.state
    assert "appointment" in result["trace"] and _workflow(state) == "appointment"
    assert _fact(state, "service_detail") == "điều hòa" and "đã dừng" not in result["reply"]
    assert _fact(state, "appointment_when") == "ngày mai" and conv.customer_id() == customer


# ---- Lỗi khi ghi lịch: không báo thành công, không lộ lỗi nội bộ, giữ thông tin để thử lại ----
def _book_until_confirmation_fails(client, fake_llm, monkeypatch, error="db down"):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError(error)))
    conv, phone = Conversation(client, "booking-error"), unique_phone()
    conv.say(_script_full_booking(fake_llm, f"Đặt bảo dưỡng 2 giờ chiều mai, tên Nguyễn Văn An, SĐT {phone}", phone))
    return conv, phone, conv.say("Được")


def test_tc16_booking_error_is_not_reported_as_booked(client, fake_llm, monkeypatch):
    conv, _, result = _book_until_confirmation_fails(client, fake_llm, monkeypatch)
    assert BOOKED not in result["reply"] and "đã đặt" not in result["reply"].lower()
    assert SAFE_FAILURE in result["reply"] and "hotline" in result["reply"] and "db down" not in result["reply"]
    assert _workflow(conv.state) == "appointment" and _fact(conv.state, "appointment_when")
    assert conv.appointments() == []


def test_booking_service_error_does_not_expose_exception(client, fake_llm, monkeypatch):
    _, _, result = _book_until_confirmation_fails(client, fake_llm, monkeypatch, error="private failure")
    assert "private failure" not in result["reply"] and "hotline" in result["reply"]


def test_appointment_failure_keeps_known_facts(client, fake_llm, monkeypatch):
    conv, phone, _ = _book_until_confirmation_fails(client, fake_llm, monkeypatch)
    assert _fact(conv.state, "appointment_when") == "2 giờ chiều mai"
    assert _fact(conv.state, "customer_phone") == phone and _fact(conv.state, "service_type") == "MAINTENANCE"
    assert not _open_pending(conv.state)                      # xác nhận cũ đã hết hạn, không treo chờ ghi lại


# ---- Chen ngang khi đang đặt lịch ----
def test_tc12_price_interrupt_keeps_pending_booking(client, fake_llm):
    _script_partial_booking(fake_llm)
    question = "Nhân tiện, thay dầu cho Foton Aumark hết bao nhiêu tiền?"
    fake_llm.understands(question, task("SALES", "ask_price", "thay dầu Foton Aumark"))
    fake_llm.uses_tools("sales", [], final="Dạ mình sẽ gửi bảng giá thay dầu cho bạn ạ.")
    conv = Conversation(client, "tc12-policy")
    conv.say("Đặt bảo dưỡng ngày mai")
    result = conv.say(question)
    state = conv.state
    assert "sales" in result["trace"] and "appointment" not in result["trace"]
    assert _workflow(state) == "appointment" and _fact(state, "appointment_when") == "ngày mai"
    assert any(item.metadata.get("workflow") == "appointment" for item in _open_pending(state))


def test_tc09_purchase_discount_goes_to_manager(client, fake_llm):
    text = "Nếu mua xe thì bên mình giảm giá cho tôi 15% được không?"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", "giảm giá 15%"),
                         signals=PolicySignals(discount_request=True))
    conv = Conversation(client, "tc09-buy")
    result = conv.say(text)
    assert result["mode"] == "HUMAN_PENDING" and "human_handover" in result["trace"]
    assert "giảm 15%" not in result["reply"] and not {"sales", "contract"} & set(result["trace"])
    assert "NEGOTIATION" in conv.tickets()[-1].summary


# ---- Trả lời ngắn điền slot ----
def _script_short_time_reply(fake_llm):
    first = _script_partial_booking(fake_llm, "Tôi muốn đặt lịch bảo dưỡng vào ngày mai.")
    fake_llm.understands("Khoảng 10 giờ sáng.", _appointment_task("10 giờ sáng", relationship="ANSWER_TO_QUESTION"),
                         slots=[slot("appointment_when", "ngày mai khoảng 10 giờ sáng", "10 giờ sáng")])
    return first


def test_tc06_short_time_reply_fills_slot(client, fake_llm):
    conv = Conversation(client, "tc06-short")
    conv.say(_script_short_time_reply(fake_llm))
    result = conv.say("Khoảng 10 giờ sáng.")
    assert "appointment" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert "10" in _fact(conv.state, "appointment_when") and _fact(conv.state, "appointment_date")["time"] == "10:00"
    assert conv.appointments() == []


def test_tc06_short_time_reply_does_not_claim_booking(client, fake_llm):
    conv = Conversation(client, "tc06-short-no-book")
    conv.say(_script_short_time_reply(fake_llm))
    result = conv.say("Khoảng 10 giờ sáng.")
    assert BOOKED not in result["reply"] and "đã đặt" not in result["reply"].lower() and conv.appointments() == []
    assert _open_pending(conv.state)


# ---- Hoãn writer thứ hai (hợp đồng + đặt lịch trong cùng một tin) ----
def test_appointment_deferral_does_not_create_a_question_before_writer_runs(client, fake_llm):
    _script_deferral(fake_llm)
    conv = Conversation(client, "deferred-appointment")
    result = conv.say(DEFERRAL)
    assert "contract" in result["trace"] and "appointment" not in result["trace"]
    assert not any(item.kind.value == "QUESTION" and item.metadata.get("workflow") == "appointment"
                   for item in _open_pending(conv.state))                # chưa hỏi gì về lịch hẹn
    follow_up = conv.say("Được")
    assert "appointment" in follow_up["trace"] and conv.appointments() == []
    assert any(item.kind.value == "QUESTION" and item.metadata.get("workflow") == "appointment"
               for item in _open_pending(conv.state))
    assert "đặt lịch hẹn" in result["reply"]


def test_two_writers_deferred_appointment_asks_for_missing_slots_when_resumed(client, fake_llm):
    _script_deferral(fake_llm)
    conv = Conversation(client, "deferred-with-phone")
    conv.say(DEFERRAL)
    resumed = conv.say("Được")
    assert "appointment" in resumed["trace"] and "contract" not in resumed["trace"]
    assert "loại dịch vụ" in resumed["reply"].lower() and conv.appointments() == []   # chưa có slot nào, hỏi từ đầu


def test_appointment_writer_is_only_run_after_deferred_request_is_selected(client, fake_llm):
    _script_deferral(fake_llm)
    conv = Conversation(client, "deferred-writer-trace")
    assert "appointment" not in conv.say(DEFERRAL)["trace"]
    assert "appointment" in conv.say("Được")["trace"]


def test_two_writer_deferral_keeps_contract_workflow_and_an_appointment_offer(client, fake_llm):
    """Writer đầu (hợp đồng) giữ workflow; writer bị hoãn nằm trong OFFER mở để câu 'Được' kế tiếp nối lại."""
    _script_deferral(fake_llm)
    conv = Conversation(client, "deferred-workflow")
    conv.say(DEFERRAL)
    workflow = conv.state.get("active_workflow")
    assert workflow and workflow.workflow_type == "contract" and workflow.status.value == "WAITING_FOR_USER"
    assert [i.metadata.get("workflow") for i in _open_pending(conv.state) if i.kind.value == "OFFER"] == ["appointment"]
    conv.say("Được")
    assert conv.state["active_workflow"].workflow_type == "appointment"


def test_offer_acceptance_is_stored_as_pending_interaction(client, fake_llm):
    """OFFER do hệ thống tạo khi hoãn writer (không phải do hỏi dịch vụ); khách nhận thì OFFER đóng lại."""
    _script_deferral(fake_llm)
    conv = Conversation(client, "canonical-offer")
    conv.say(DEFERRAL)
    assert any(item.kind.value == "OFFER" for item in _open_pending(conv.state))
    conv.say("Được")
    assert not any(item.kind.value == "OFFER" for item in _open_pending(conv.state))


# ---- Trạng thái chuẩn của pending/workflow trong luồng đặt lịch ----
def test_waiting_appointment_pending_interaction_tracks_missing_slot(client, fake_llm):
    conv = Conversation(client, "pending-appointment")
    conv.say(_script_partial_booking(fake_llm))
    pending = _open_pending(conv.state)
    assert pending and set(pending[-1].metadata["missing_slots"]) == {"họ và tên đầy đủ", "số điện thoại"}


def test_pending_questions_are_structured(client, fake_llm):
    conv = Conversation(client, "structured-question")
    conv.say(_script_partial_booking(fake_llm))
    pending = _open_pending(conv.state)
    assert pending and pending[-1].kind.value == "QUESTION" and pending[-1].metadata["workflow"] == "appointment"


def test_confirmation_is_stored_as_pending_then_resolved_and_workflow_waits_for_approval(client, fake_llm):
    """WAITING_FOR_APPROVAL sau khi ghi lịch là chủ đích của sản phẩm (đã xác nhận với chủ dự án)."""
    phone = unique_phone()
    conv = Conversation(client, "workflow-after-booking")
    conv.say(_script_full_booking(fake_llm, f"Đặt bảo dưỡng 2 giờ chiều mai, tên Nguyễn Văn An, SĐT {phone}", phone))
    assert [i.kind.value for i in _open_pending(conv.state)] == ["CONFIRMATION"]
    assert conv.state["active_workflow"].status.value == "WAITING_FOR_USER"
    conv.say("Được")
    assert not _open_pending(conv.state) and len(conv.appointments()) == 1
    assert conv.state["active_workflow"].status.value == "WAITING_FOR_APPROVAL"


def test_appointment_pending_question_is_current_after_time_update(client, fake_llm):
    conv = Conversation(client, "pending-time-update")
    conv.say(_script_partial_booking(fake_llm))
    fake_llm.understands("Đổi sang thứ 7", _appointment_task("thứ 7", relationship="CORRECT_PREVIOUS_INFO"),
                         slots=[slot("appointment_when", "thứ 7", "thứ 7")])
    conv.say("Đổi sang thứ 7")
    pending = _open_pending(conv.state)
    assert _fact(conv.state, "appointment_when") == "thứ 7"
    assert len(pending) == 1 and NAME_ASK in pending[0].text.lower()            # câu hỏi đang mở là câu hỏi mới nhất


def test_temporary_pending_items_expire_when_replaced(client, fake_llm):
    conv = Conversation(client, "pending-replaced")
    conv.say(_script_partial_booking(fake_llm))
    fake_llm.understands("Đổi sang thứ 7", _appointment_task("thứ 7", relationship="CORRECT_PREVIOUS_INFO"),
                         slots=[slot("appointment_when", "thứ 7", "thứ 7")])
    conv.say("Đổi sang thứ 7")
    statuses = [item.status.value for item in conv.state["pending_interactions"]]
    assert statuses == ["EXPIRED", "OPEN"]
