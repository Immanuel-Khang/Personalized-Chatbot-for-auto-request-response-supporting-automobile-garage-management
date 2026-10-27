"""Văn phong trả lời: không chào lại giữa hội thoại, chưa rõ ý thì hỏi lại thay vì đoán."""
import pytest

from app.agent.nodes.common import strip_greeting
from app.agent.nodes.compose import compose
from app.agent.nodes.router import router
from app.contracts.schemas import (
    Intent,
    ActiveWorkflow,
    ConversationInterpretation,
    TaskInterpretation,
    TaskRelationship,
    WorkflowStatus,
)


def _interpretation(domain, action, text, *, uncertain=False):
    return ConversationInterpretation(
        message_summary=text,
        tasks=[TaskInterpretation(
            task_id="style-task",
            domain=domain,
            action=action,
            text=text,
            relationship=TaskRelationship.UNCLEAR if uncertain else TaskRelationship.NEW_REQUEST,
            requires_clarification=uncertain,
        )],
        uncertain=uncertain,
    )


@pytest.mark.parametrize("text, expected", [
    ("Chào Khang,\n\nBạn có thể liên hệ hotline", "Bạn có thể liên hệ hotline"),
    ("Xin chào bạn! Giá xe là", "Giá xe là"),
    ("Chào anh/chị. Xe này", "Xe này"),
    ("Chào mừng bạn đến với xưởng", "Chào mừng bạn đến với xưởng"),  # không phải lời chào mở đầu
    ("Giá xe Foton", "Giá xe Foton"),
])
def test_strip_greeting(text, expected):
    assert strip_greeting(text) == expected


def _draft(domain):
    answers = [{"agent": "conversation", "order": 0, "text": "Chào Khang,\n\nHotline là 0900", "needs_human": False}]
    interpretation = _interpretation(domain, "general_conversation", "")
    return compose({"conversation_interpretation": interpretation, "answers": answers})["draft_reply"]


def test_compose_strips_greeting_unless_customer_greets():
    assert _draft(Intent.OTHER) == "Hotline là 0900"
    assert _draft(Intent.GREETING).startswith("Chào Khang")


def _appointment_workflow():
    return ActiveWorkflow(
        workflow_id="style-appointment",
        workflow_type="appointment",
        status=WorkflowStatus.ACTIVE,
    )


def test_unclear_message_in_active_flow_asks_back():
    text = "Liền hề náy khổng chịnh xạc"
    update = router({
        "conversation_interpretation": _interpretation(Intent.OTHER, "general_conversation", text, uncertain=True),
        "user_text": text,
        "active_workflow": _appointment_workflow(),
    })
    assert update["planned_agents"] == ["conversation"]


def test_affirmation_continues_active_flow():
    text = "Ok"
    interpretation = _interpretation(Intent.APPOINTMENT, "book_appointment", text)
    interpretation.answers_pending_question = True
    update = router({
        "conversation_interpretation": interpretation,
        "user_text": text,
        "active_workflow": _appointment_workflow(),
    })
    assert update["planned_agents"] == ["appointment"]


def test_out_of_scope_without_flow_is_not_clarify():
    text = "Bạn thích ăn gì"
    update = router({
        "conversation_interpretation": _interpretation(Intent.OTHER, "general_conversation", text),
        "user_text": text,
    })
    assert "route_reason" not in update
