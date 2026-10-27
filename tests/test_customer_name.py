"""Customer name: validator, validated LLM slot updates, and the appointment node."""
import pytest

from app.agent.nodes import preprocess as pre
from app.agent.nodes.appointment import appointment
from app.agent.nodes.preprocess import _apply_interpretation, _valid_name
from app.contracts.schemas import (
    ConversationInterpretation, FactSource, Intent, KnownFact, SlotOperation, SlotUpdate,
    TaskInterpretation, TaskRelationship,
)


def _interpretation(text, name, relationship=TaskRelationship.NEW_REQUEST, evidence=None):
    task = TaskInterpretation(task_id="t1", domain=Intent.APPOINTMENT, action="book_appointment",
                              text=text, relationship=relationship)
    return ConversationInterpretation(
        message_summary="Khách đặt lịch.", tasks=[task],
        slot_updates=[SlotUpdate(key="customer_name", operation=SlotOperation.SET, value=name,
                                 evidence=evidence or name)],
    )


def _known(name):
    return {"customer_name": KnownFact(key="customer_name", value=name, source=FactSource.USER_EXPLICIT)}


@pytest.mark.parametrize("value", ["Nam", "Minh Tuấn", "Nguyễn Văn A", "  Lan  ", "Nguyễn Thị Thu Hà"])
def test_valid_name_accepts_person_names(value):
    assert _valid_name(value) == value.strip()


@pytest.mark.parametrize("value", [
    "", "   ", None, 10, "10 tấn", "xe tải", "ngày mai", "0912345678", "+84912345678", "Xin chào",
    "khách ở Hà Nội", "Hà Nội", "Foton Auman", "người cần xem xe", "Nam 2", "a" * 80,
    "Một Hai Ba Bốn Năm Sáu",
])
def test_valid_name_rejects_non_names(value):
    assert _valid_name(value) is None


def test_llm_name_update_is_stored_as_explicit_fact():
    text = "Em Lan, muốn đặt lịch bảo dưỡng"
    facts = _apply_interpretation({"user_text": text, "known_facts": {}}, _interpretation(text, "Lan"))["known_facts"]
    assert facts["customer_name"].value == "Lan"
    assert facts["customer_name"].source == FactSource.USER_EXPLICIT


def test_llm_bare_name_reply_is_stored():
    facts = _apply_interpretation({"user_text": "Lan", "known_facts": {}}, _interpretation("Lan", "Lan"))["known_facts"]
    assert facts["customer_name"].value == "Lan"


def test_llm_name_with_phone_in_same_message_is_cleaned():
    text = "Nguyễn Văn A, sdt 0912345678"
    facts = _apply_interpretation({"user_text": text, "known_facts": {}},
                                  _interpretation(text, "Nguyễn Văn A, sdt", evidence="Nguyễn Văn A"))["known_facts"]
    # the value carries a stop word, so it must be dropped rather than stored dirty
    assert "customer_name" not in facts


@pytest.mark.parametrize("bad", ["10 tấn", "xe tải", "ngày mai", "0912345678", "Xin chào"])
def test_llm_non_name_update_is_discarded(bad):
    facts = _apply_interpretation({"user_text": bad, "known_facts": {}}, _interpretation(bad, bad))["known_facts"]
    assert "customer_name" not in facts


def test_known_name_is_not_overwritten_without_correction():
    text = "Tôi tên Bình"
    state = {"user_text": text, "known_facts": _known("Nam")}
    facts = _apply_interpretation(state, _interpretation(text, "Bình"))["known_facts"]
    assert facts["customer_name"].value == "Nam"


def test_known_name_can_be_corrected_explicitly():
    text = "Không phải, tôi tên Bình"
    state = {"user_text": text, "known_facts": _known("Nam")}
    interpretation = _interpretation(text, "Bình", TaskRelationship.CORRECT_PREVIOUS_INFO)
    facts = _apply_interpretation(state, interpretation)["known_facts"]
    assert facts["customer_name"].value == "Bình"
    assert facts["customer_name"].source == FactSource.USER_CORRECTED


def test_preprocess_without_llm_creates_no_name(monkeypatch):
    # No LLM -> neutral interpretation; preprocessing never guesses a name from raw text.
    monkeypatch.setattr(pre, "_write_preprocess_trace", lambda result: None)
    monkeypatch.setattr(pre, "generate_structured", lambda *a, **k: None)
    assert "customer_name" not in pre.preprocess({"user_text": "Tôi tên Nam", "known_facts": {}})["known_facts"]


def test_appointment_keeps_name_and_does_not_ask_for_it(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.appointment.generate", lambda *args, **kwargs: None)
    text = "Em Lan muốn đặt lịch bảo dưỡng ngày mai"
    interpretation = _interpretation(text, "Lan", evidence="Lan")
    interpretation.slot_updates.extend([
        SlotUpdate(key="service_type", operation=SlotOperation.SET, value="MAINTENANCE", evidence="bảo dưỡng"),
        SlotUpdate(key="appointment_when", operation=SlotOperation.SET, value="ngày mai", evidence="ngày mai"),
    ])
    out = appointment({"user_text": text, "known_facts": {}, "conversation_interpretation": interpretation})
    assert out["known_facts"]["customer_name"].value == "Lan"
    assert "customer_phone" in out["pending_interactions"][-1].metadata["missing_slots"]
    assert "customer_name" not in out["pending_interactions"][-1].metadata["missing_slots"]


def test_appointment_without_name_still_asks_only_required_slots(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.appointment.generate", lambda *args, **kwargs: None)
    out = appointment({"user_text": "đặt lịch bảo dưỡng ngày mai", "known_facts": {}})
    assert "customer_name" not in out["pending_interactions"][-1].metadata["missing_slots"]
