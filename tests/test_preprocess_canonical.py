import json
from types import SimpleNamespace

from app.agent.nodes.preprocess import _apply_interpretation, _trace_safe, validate_interpretation
from app.contracts.schemas import (
    ConversationInterpretation,
    EntityReference,
    FactSource,
    Intent,
    KnownFact,
    SlotOperation,
    SlotUpdate,
    TaskInterpretation,
    TaskRelationship,
)


def _product(product_id: int, name: str):
    return SimpleNamespace(id=product_id, name=name)


def _task(domain=Intent.SALES, action="show_catalog", text="Cho xem danh sách xe"):
    return TaskInterpretation(
        task_id="task_1", domain=domain, action=action, text=text,
        relationship=TaskRelationship.NEW_REQUEST,
    )


def test_canonical_interpretation_keeps_explicit_catalog_action():
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn xem danh sách xe.",
        tasks=[_task()],
        catalog_request=True,
    )

    assert interpretation.tasks[0].action == "show_catalog"
    assert interpretation.catalog_request


SHOWN = [{"entity_type": "PRODUCT", "entity_id": 11, "name": "Xe A"},
         {"entity_type": "PRODUCT", "entity_id": 22, "name": "Xe B"}]


def _ref_interpretation(position, entity_id=None):
    return ConversationInterpretation(
        message_summary="Khách hỏi xe trong danh sách.",
        tasks=[_task(action="product_detail", text="xe thứ 2")],
        references=[EntityReference(mention="xe thứ 2", position=position, entity_id=entity_id)],
    )


def test_product_ordinal_resolves_against_last_displayed_list():
    state = {"user_text": "Xe thứ 2 giá bao nhiêu", "selected_entities": SHOWN}
    applied = validate_interpretation(state, _ref_interpretation(2))
    assert applied.references[0].entity_id == 22 and not applied.references[0].ambiguous
    assert applied.tasks[0].entity_id == 22 and applied.tasks[0].entity_type == "PRODUCT"


def test_product_reference_without_displayed_history_requires_clarification():
    state = {"user_text": "Tôi muốn xem xe số 1", "selected_entities": []}
    applied = validate_interpretation(state, _ref_interpretation(1, entity_id=99))
    assert applied.references[0].entity_id is None and applied.product_reference_ambiguous
    assert applied.tasks[0].entity_id is None


def test_conflicting_position_and_id_is_ambiguous():
    state = {"user_text": "xe thứ 2", "selected_entities": SHOWN}
    applied = validate_interpretation(state, _ref_interpretation(2, entity_id=11))
    assert applied.references[0].ambiguous and applied.tasks[0].entity_id is None


def test_slot_updates_require_current_message_evidence_and_apply_corrections():
    state = {
        "user_text": "Không phải ngày mai, thứ 3 tuần sau",
        "known_facts": {"appointment_when": KnownFact(
            key="appointment_when", value="ngày mai", source=FactSource.USER_EXPLICIT,
        )},
    }
    interpretation = ConversationInterpretation(
        message_summary="Khách sửa ngày hẹn.",
        tasks=[_task(Intent.APPOINTMENT, "book_appointment", state["user_text"])],
        slot_updates=[
            SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                       value="thứ 3 tuần sau", evidence="thứ 3 tuần sau"),
            SlotUpdate(key="customer_phone", operation=SlotOperation.SET,
                       value="0901234567", evidence="0901234567"),
        ],
    )

    applied = validate_interpretation(state, interpretation)
    result = _apply_interpretation(state, applied)

    assert len(result["conversation_interpretation"].slot_updates) == 1
    fact = result["known_facts"]["appointment_when"]
    assert fact.value == "thứ 3 tuần sau"
    assert fact.source == FactSource.USER_CORRECTED
    assert "customer_phone" not in result["known_facts"]


def test_trace_safe_serializes_canonical_result_and_redacts_personal_data():
    result = {
        "conversation_interpretation": ConversationInterpretation(
            message_summary="Khách muốn xem danh sách xe.", tasks=[_task()],
        ),
        "known_facts": {"customer_phone": KnownFact(
            key="customer_phone", value="0901234567", source=FactSource.USER_EXPLICIT,
        )},
        "trace": ["preprocess"],
    }

    parsed = json.loads(json.dumps(_trace_safe(result), ensure_ascii=False))

    assert set(parsed) == {"conversation_interpretation", "known_facts", "trace"}
    assert parsed["conversation_interpretation"]["tasks"][0]["action"] == "show_catalog"
    assert parsed["known_facts"]["customer_phone"] == "[REDACTED]"
    assert "entity_id" in parsed["conversation_interpretation"]["tasks"][0]
    assert _trace_safe({"customer_phone": "0901234567", "evidence": "My number 0901234567"}) == {
        "customer_phone": "[REDACTED]", "evidence": "[REDACTED]"
    }


def test_showroom_booking_does_not_invent_intended_use():
    state = {"user_text": "xem xe số 1 trực tiếp, giúp tôi đặt lịch"}
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn xem xe trực tiếp và đặt lịch.",
        tasks=[
            _task(Intent.APPOINTMENT, "book_appointment", "giúp tôi đặt lịch"),
        ],
        slot_updates=[],
    )

    assert not any(update.key == "intended_use" for update in validate_interpretation(state, interpretation).slot_updates)
    assert not interpretation.slot_updates


def test_uncorroborated_fact_is_not_written_to_known_facts():
    state = {"user_text": "Tôi muốn đặt lịch lái thử", "known_facts": {}}
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn đặt lịch lái thử.",
        tasks=[_task(Intent.APPOINTMENT, "book_appointment", state["user_text"])],
        slot_updates=[SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                                 value="ngày mai", evidence="ngày mai")],
    )

    assert _apply_interpretation(state, interpretation)["known_facts"] == {}
    assert isinstance(interpretation, ConversationInterpretation)


def test_explicit_fact_is_persisted_as_user_explicit():
    text = "Tôi muốn đặt lịch ngày mai"
    interpretation = ConversationInterpretation(
        message_summary="Khách muốn đặt lịch.",
        tasks=[_task(Intent.APPOINTMENT, "book_appointment", text)],
        slot_updates=[SlotUpdate(key="appointment_when", operation=SlotOperation.SET,
                                 value="ngày mai", evidence="ngày mai")],
    )

    fact = _apply_interpretation({"user_text": text, "known_facts": {}}, interpretation)["known_facts"]["appointment_when"]
    assert fact.value == "ngày mai"
    assert fact.source == FactSource.USER_EXPLICIT


def test_empty_interpretation_is_valid_canonical_output():
    result = ConversationInterpretation(message_summary="Không có yêu cầu hành động.")

    assert result.tasks == []
    assert result.slot_updates == []
    assert not result.uncertain


def test_fact_correction_does_not_mutate_prior_state():
    old = KnownFact(key="budget_vnd", value=500_000_000, source=FactSource.USER_EXPLICIT)
    state = {"user_text": "Tăng ngân sách lên 700 triệu", "known_facts": {"budget_vnd": old}}
    interpretation = ConversationInterpretation(
        message_summary="Khách sửa ngân sách.",
        tasks=[_task(text=state["user_text"])],
        slot_updates=[SlotUpdate(key="budget_vnd", operation=SlotOperation.SET,
                                 value=700_000_000, evidence="700 triệu")],
    )

    result = _apply_interpretation(state, interpretation)

    assert old.value == 500_000_000
    assert result["known_facts"]["budget_vnd"].value == 700_000_000
    assert result["known_facts"]["budget_vnd"].source == FactSource.USER_CORRECTED
    assert "budget_vnd" not in state["known_facts"] or state["known_facts"]["budget_vnd"].value == 500_000_000


def test_trace_safe_does_not_expose_evidence_in_nested_slot_updates():
    value = ConversationInterpretation(
        message_summary="Số điện thoại khách.",
        slot_updates=[SlotUpdate(key="customer_phone", operation=SlotOperation.SET,
                                 value="0901234567", evidence="0901234567")],
    )

    serialized = _trace_safe(value)

    assert serialized["slot_updates"][0]["evidence"] == "[REDACTED]"
    assert serialized["slot_updates"][0]["value"] == "[REDACTED]"


def test_no_current_evidence_means_no_slot_update():
    state = {"user_text": "Cho tôi xem danh sách xe", "known_facts": {}}
    update = SlotUpdate(key="budget_vnd", operation=SlotOperation.SET,
                        value=500_000_000, evidence="500 triệu")
    interpretation = ConversationInterpretation(message_summary="Khách muốn xem xe.", slot_updates=[update])

    assert _apply_interpretation(state, interpretation)["known_facts"] == {}


def test_non_fact_task_output_remains_canonical():
    task = TaskInterpretation(
        task_id="task_2", domain=Intent.OTHER, action="general_conversation", text="Xin chào",
        relationship=TaskRelationship.NEW_REQUEST,
    )
    result = ConversationInterpretation(message_summary="Khách chào hỏi.", tasks=[task])

    assert result.tasks == [task]
    assert result.slot_updates == []
    assert result.tasks[0].domain == Intent.OTHER


def test_redaction_keeps_non_sensitive_catalog_data():
    task = TaskInterpretation(
        task_id="task_3", domain=Intent.SALES, action="show_catalog", text="Danh sách xe",
        relationship=TaskRelationship.NEW_REQUEST, entity_id=12, entity_type="PRODUCT",
    )
    result = _trace_safe(ConversationInterpretation(message_summary="Catalog", tasks=[task]))

    assert result["tasks"][0]["entity_id"] == 12
    assert result["tasks"][0]["entity_type"] == "PRODUCT"
