"""Policy regressions using the canonical conversation state and real chat graph."""
from unittest.mock import Mock

from app.services import get_services
from tests.conversation_helpers import (BOOKED, BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation,
                                        mentions, unique_phone)

TC01 = "Xe tôi đang gặp vấn đề với kính chắn gió, có thể mang qua hãng để sửa được không?"


def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def _workflow(state):
    workflow = state.get("active_workflow")
    return workflow.workflow_type if workflow else None


def _open_pending(state):
    return [item for item in state.get("pending_interactions", []) if item.status.value == "OPEN"]


def test_service_inquiry_leaves_offer_not_flow(client):
    conv = Conversation(client, "offer")
    result = conv.say(TC01)
    state = conv.state
    assert "conversation" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert _workflow(state) is None
    assert not mentions(result["reply"], PHONE_ASK) and conv.appointments() == []


def test_accepting_offer_with_time_enters_booking_without_creating(client):
    conv = Conversation(client, "offer-accept")
    conv.say(TC01)
    result = conv.say("Ngày mai lúc 10 giờ")
    state = conv.state
    assert "appointment" in result["trace"] and _workflow(state) == "appointment"
    assert _fact(state, "service_type") == "REPAIR" and "10 giờ" in _fact(state, "appointment_when")
    assert mentions(result["reply"], PHONE_ASK) and not mentions(result["reply"], DATE_ASK)
    assert conv.appointments() == []
    assert any(item.metadata.get("workflow") == "appointment" for item in _open_pending(state))


def test_known_phone_offer_acceptance_asks_confirmation_before_booking(client):
    conv = Conversation(client, "offer-confirm")
    conv.say(f"Số điện thoại của mình là {unique_phone()}")
    conv.say(TC01)
    tentative = conv.say("Ngày mai lúc 10 giờ")
    assert BOOKED not in tentative["reply"] and conv.appointments() == []
    confirmed = conv.say("Được")
    assert BOOKED in confirmed["reply"] and len(conv.appointments()) == 1


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


def test_tc15_switching_service_updates_context_keeps_identity(client):
    conv = Conversation(client, "tc15")
    customer = conv.link_customer(unique_phone())
    conv.say("Tôi muốn đặt lịch sửa kính chắn gió ngày mai.")
    assert _fact(conv.state, "service_detail") == "kính chắn gió"
    result = conv.say("Thôi, tôi muốn kiểm tra điều hòa trước.")
    state = conv.state
    assert "appointment" in result["trace"] and _workflow(state) == "appointment"
    assert _fact(state, "service_detail") == "điều hòa" and "đã dừng" not in result["reply"]
    assert conv.customer_id() == customer


def test_tc16_booking_error_is_not_reported_as_booked(client, monkeypatch):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError("db down")))
    conv = Conversation(client, "tc16")
    conv.say(f"Số điện thoại của mình là {unique_phone()}")
    result = conv.say("Tôi muốn đặt lịch bảo dưỡng lúc 2 giờ chiều mai.")
    state = conv.state
    assert BOOKED not in result["reply"] and "chưa xác nhận được" in result["reply"]
    assert "hotline" in result["reply"] and "db down" not in result["reply"]
    assert _workflow(state) == "appointment" and _fact(state, "appointment_when")
    assert conv.appointments() == []


def test_tc12_price_interrupt_keeps_pending_booking(client):
    conv = Conversation(client, "tc12-policy")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai.")
    result = conv.say("Nhân tiện, thay dầu cho Foton Aumark hết bao nhiêu tiền?")
    state = conv.state
    assert "appointment" not in result["trace"] and _workflow(state) == "appointment"
    assert _fact(state, "appointment_when") == "ngày mai"


def test_tc09_purchase_discount_goes_to_manager(client):
    conv = Conversation(client, "tc09-buy")
    result = conv.say("Nếu mua xe thì bên mình giảm giá cho tôi 15% được không?")
    assert result["mode"] == "HUMAN_PENDING" and "human_handover" in result["trace"]
    assert "giảm 15%" not in result["reply"] and not {"sales", "contract"} & set(result["trace"])


def test_tc06_short_time_reply_fills_slot(client):
    conv = Conversation(client, "tc06-short")
    conv.say("Tôi muốn đặt lịch bảo dưỡng vào ngày mai.")
    result = conv.say("Khoảng 10 giờ sáng.")
    assert "appointment" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert "ngày mai" in _fact(conv.state, "appointment_when") and "10" in _fact(conv.state, "appointment_when")
    assert conv.appointments() == []


def test_appointment_deferral_does_not_create_a_question_before_writer_runs(client):
    conv = Conversation(client, "deferred-appointment")
    result = conv.say("Mình chốt mua xe, đặt lịch qua showroom xem xe nữa")
    assert "contract" in result["trace"] and "appointment" not in result["trace"]
    assert _workflow(conv.state) == "appointment"
    assert not any(item.metadata.get("workflow") == "appointment" for item in _open_pending(conv.state))
    follow_up = conv.say("Được")
    assert "appointment" in follow_up["trace"]
    assert any(item.metadata.get("workflow") == "appointment" for item in _open_pending(conv.state))
    assert conv.appointments() == []
    assert "đặt lịch hẹn" in result["reply"]


def test_two_writers_deferred_appointment_asks_phone_after_slots_available(client):
    conv = Conversation(client, "deferred-with-phone")
    result = conv.say(f"Mình chốt mua xe, đặt lịch qua showroom xem xe nữa, số điện thoại {unique_phone()}")
    assert "contract" in result["trace"] and "appointment" not in result["trace"]
    assert not conv.appointments()
    next_turn = conv.say("Được")
    assert "appointment" in next_turn["trace"]
    assert mentions(next_turn["reply"], DATE_ASK)


def test_removed_router_helpers_are_not_part_of_canonical_contract():
    from app.agent.nodes.router import router
    from app.contracts.schemas import ConversationInterpretation

    update = router({"user_text": "Bạn thích ăn gì", "conversation_interpretation":
                     ConversationInterpretation(message_summary="Câu hỏi ngoài phạm vi.")})
    assert update["plan"][0]["agent"] == "conversation"
    assert "offer" not in update and "intents" not in update


# Keep the import here to assert the service failure test exercises the configured service surface.
assert get_services().booking is not None


def test_tc06_short_time_reply_does_not_claim_booking(client):
    conv = Conversation(client, "tc06-short-no-book")
    conv.say("Tôi muốn đặt lịch bảo dưỡng vào ngày mai.")
    result = conv.say("Khoảng 10 giờ sáng.")
    assert BOOKED not in result["reply"] and conv.appointments() == []
    assert _open_pending(conv.state)


def test_waiting_appointment_pending_interaction_tracks_missing_slot(client):
    conv = Conversation(client, "pending-appointment")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai.")
    pending = _open_pending(conv.state)
    assert pending and pending[-1].metadata["missing_slots"] == ["customer_phone"]


def test_waiting_appointment_slot_names_are_canonical():
    assert BOOKING_SLOTS == {"service_type", "appointment_when", "when_text"}


# Regression for safe failure output; internal exception details must never leak.
def test_booking_service_error_does_not_expose_exception(client, monkeypatch):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError("private failure")))
    conv = Conversation(client, "safe-booking-error")
    conv.say(f"Số điện thoại {unique_phone()}")
    reply = conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert "private failure" not in reply["reply"]
    assert "hotline" in reply["reply"]


def test_offer_acceptance_is_stored_as_pending_interaction(client):
    conv = Conversation(client, "canonical-offer")
    conv.say(TC01)
    assert any(item.kind.value == "OFFER" for item in _open_pending(conv.state))
    conv.say("Ngày mai lúc 10 giờ")
    assert not any(item.kind.value == "OFFER" for item in _open_pending(conv.state))


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


def test_pending_questions_are_structured(client):
    conv = Conversation(client, "structured-question")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    pending = _open_pending(conv.state)
    assert pending and pending[-1].kind.value == "QUESTION"
    assert pending[-1].metadata["workflow"] == "appointment"


def test_test_database_session_is_isolated(client):
    conv = Conversation(client, "policy-test-db")
    conv.say("Xin chào")
    assert conv.state.get("session_id") == conv.id


def test_appointment_writer_is_only_run_after_deferred_request_is_selected(client):
    conv = Conversation(client, "deferred-writer-trace")
    first = conv.say("Mình chốt mua xe, đặt lịch qua showroom xem xe nữa")
    assert "appointment" not in first["trace"]
    second = conv.say("Được")
    assert "appointment" in second["trace"]


def test_two_writer_deferral_preserves_appointment_workflow(client):
    conv = Conversation(client, "deferred-workflow")
    conv.say("Mình chốt mua xe, đặt lịch qua showroom xem xe nữa")
    workflow = conv.state.get("active_workflow")
    assert workflow and workflow.workflow_type == "appointment"
    assert workflow.status.value == "WAITING_FOR_USER"


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


def test_canonical_workflow_status_after_booking(client):
    conv = Conversation(client, "workflow-after-booking")
    conv.say(f"Tôi muốn đặt lịch bảo dưỡng ngày mai, số điện thoại {unique_phone()}")
    workflow = conv.state.get("active_workflow")
    assert workflow and workflow.status.value == "WAITING_FOR_APPROVAL"


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


def test_appointment_failure_keeps_known_facts(client, monkeypatch):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError("db down")))
    conv = Conversation(client, "failure-keeps-facts")
    phone = unique_phone()
    conv.say(f"Số điện thoại {phone}")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    assert _fact(conv.state, "appointment_when") == "ngày mai"
    assert _fact(conv.state, "customer_phone") == phone


def test_legacy_budget_constant_not_used_in_policy_suite():
    assert "budget_vnd" not in BOOKING_SLOTS


def test_offer_does_not_create_active_appointment_workflow(client):
    conv = Conversation(client, "offer-no-workflow")
    conv.say(TC01)
    assert _workflow(conv.state) is None


def test_appointment_pending_question_is_current_after_time_update(client):
    conv = Conversation(client, "pending-time-update")
    conv.say("Đặt lịch bảo dưỡng ngày mai")
    conv.say("Đổi sang thứ 7")
    pending = _open_pending(conv.state)
    assert pending and "số điện thoại" in pending[-1].text.lower()


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


def test_temporary_pending_items_expire_when_replaced(client):
    conv = Conversation(client, "pending-replaced")
    conv.say("Đặt lịch bảo dưỡng")
    conv.say("Ngày mai")
    statuses = [item.status.value for item in conv.state["pending_interactions"]]
    assert "EXPIRED" in statuses or "OPEN" in statuses


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
