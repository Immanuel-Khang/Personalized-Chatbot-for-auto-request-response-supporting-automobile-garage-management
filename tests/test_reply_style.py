"""Văn phong trả lời: không chào lại giữa hội thoại, chưa rõ ý thì hỏi lại thay vì đoán."""
import pytest

from app.agent.nodes.common import strip_greeting
from app.agent.nodes.compose import compose
from app.agent.nodes.router import router


@pytest.mark.parametrize("text, expected", [
    ("Chào Khang,\n\nBạn có thể liên hệ hotline", "Bạn có thể liên hệ hotline"),
    ("Xin chào bạn! Giá xe là", "Giá xe là"),
    ("Chào anh/chị. Xe này", "Xe này"),
    ("Chào mừng bạn đến với xưởng", "Chào mừng bạn đến với xưởng"),  # không phải lời chào mở đầu
    ("Giá xe Vios", "Giá xe Vios"),
])
def test_strip_greeting(text, expected):
    assert strip_greeting(text) == expected


def _draft(intents):
    answers = [{"agent": "conversation", "order": 0, "text": "Chào Khang,\n\nHotline là 0900", "needs_human": False}]
    return compose({"intents": intents, "answers": answers})["draft_reply"]


def test_compose_strips_greeting_unless_customer_greets():
    assert _draft(["OTHER"]) == "Hotline là 0900"
    assert _draft(["GREETING"]).startswith("Chào Khang")


def test_unclear_message_in_active_flow_asks_back():
    update = router({"intents": ["OTHER"], "user_text": "Liền hề náy khổng chịnh xạc", "active_flow": "appointment"})
    assert update["planned_agents"] == ["conversation"] and update["conversation_reason"] == "clarify"


def test_affirmation_continues_active_flow():
    assert router({"intents": ["OTHER"], "user_text": "Ok", "active_flow": "appointment"})["planned_agents"] == ["appointment"]


def test_out_of_scope_without_flow_is_not_clarify():
    assert router({"intents": ["OTHER"], "user_text": "Bạn thích ăn gì"})["conversation_reason"] is None
