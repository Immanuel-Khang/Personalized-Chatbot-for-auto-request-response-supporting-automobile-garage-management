"""Regression tests for canonical conversation interpretation."""
from app.agent.nodes.preprocess import _apply_interpretation, validate_interpretation
from app.contracts.schemas import (
    ConversationInterpretation,
    FactSource,
    Intent,
    KnownFact,
    SlotOperation,
    SlotUpdate,
    TaskInterpretation,
    TaskRelationship,
)


def _task(domain=Intent.SALES, action="show_catalog", text="Tôi muốn xem danh sách xe"):
    return TaskInterpretation(
        task_id="task_1", domain=domain, action=action, text=text,
        relationship=TaskRelationship.NEW_REQUEST,
    )


def test_explicit_catalog_request_maps_to_browse_action():
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn xem danh sách xe.",
        tasks=[_task()], catalog_request=True,
    )
    assert interpretation.tasks[0].action == "show_catalog"
    assert interpretation.catalog_request


def test_general_sales_request_keeps_search_action():
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn mua xe.", tasks=[_task(action="search_catalog", text="Tôi muốn mua xe")],
    )
    assert interpretation.tasks[0].action == "search_catalog"


def test_correction_updates_fact_without_mutating_prior_state():
    text = "Không phải ngày mai, thứ 3 tuần sau"
    prior = KnownFact(key="appointment_when", value="ngày mai", source=FactSource.USER_EXPLICIT)
    state = {"user_text": text, "known_facts": {"appointment_when": prior}}
    interpretation = ConversationInterpretation(
        message_summary="Khách sửa ngày hẹn.",
        tasks=[_task(Intent.APPOINTMENT, "book_appointment", text)],
        slot_updates=[SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                                 value="thứ 3 tuần sau", evidence="thứ 3 tuần sau")],
    )
    result = _apply_interpretation(state, interpretation)
    assert result["known_facts"]["appointment_when"].value == "thứ 3 tuần sau"
    assert result["known_facts"]["appointment_when"].source == FactSource.USER_CORRECTED
    assert result["conversation_interpretation"].slot_updates[0].operation == SlotOperation.CORRECT
    assert prior.value == "ngày mai"


def test_uncorroborated_slot_update_is_discarded():
    state = {"user_text": "Tôi muốn đặt lịch xem xe", "known_facts": {}}
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn đặt lịch xem xe.",
        slot_updates=[SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                                 value="ngày mai", evidence="ngày mai")],
    )
    assert validate_interpretation(state, interpretation).slot_updates == []
    assert _apply_interpretation(state, interpretation)["known_facts"] == {}


def test_explicit_current_turn_slot_update_is_persisted():
    text = "Tôi muốn đặt lịch ngày mai"
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn đặt lịch.", tasks=[_task(Intent.APPOINTMENT, "book_appointment", text)],
        slot_updates=[SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                                 value="ngày mai", evidence="ngày mai")],
    )
    facts = _apply_interpretation({"user_text": text, "known_facts": {}}, interpretation)["known_facts"]
    assert facts["appointment_when"].value == "ngày mai"
    assert facts["appointment_when"].source == FactSource.USER_EXPLICIT


def test_canonical_greeting_has_no_business_facts():
    task = TaskInterpretation(task_id="greeting", domain=Intent.OTHER,
                              action="general_conversation", text="Xin chào",
                              relationship=TaskRelationship.NEW_REQUEST)
    interpretation = ConversationInterpretation(message_summary="Khách chào hỏi.", tasks=[task])
    assert interpretation.tasks == [task]
    assert interpretation.slot_updates == []


def test_slot_update_schema_keeps_quoted_evidence():
    update = SlotUpdate(key="intended_use", operation=SlotOperation.SET,
                        value="chở hàng", evidence="chở hàng")
    assert update.key == "intended_use"
    assert update.operation == SlotOperation.SET
    assert update.evidence == "chở hàng"


def test_correction_relationship_is_canonical():
    task = _task(Intent.APPOINTMENT, "book_appointment", "Không phải ngày mai")
    task = task.model_copy(update={"relationship": TaskRelationship.CORRECT_PREVIOUS_INFO})
    assert task.relationship == TaskRelationship.CORRECT_PREVIOUS_INFO
