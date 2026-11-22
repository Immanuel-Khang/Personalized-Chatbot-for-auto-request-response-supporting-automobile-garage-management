"""Tự kiểm FakeLLM: bảo đảm bản giả cắm đúng vào app/agent/llm.py và không lệch khỏi prompt thật.
Không cần DB, không cần LLM. Nếu file này đỏ thì đừng tin các test L3 dùng fake_llm."""
from datetime import date

import pytest

from app.agent import llm
from app.agent.date_extraction import extract_date
from tests.fake_llm import (FakeLLM, TEXT_ROLE_MARKERS, fake_llm, role_of_system, role_of_tools, slot,  # noqa: F401
                            task, tool_call_spy)  # noqa: F401

TODAY = date(2026, 11, 19)  # thứ 5


# ---- vai nhận diện khớp với prompt thật (đỏ khi ai đó sửa prompt) ----
def test_text_role_markers_match_real_prompts():
    from app.agent.memory import SUMMARY_PROMPT
    from app.agent.nodes.contract import CONTRACT_SYSTEM_PROMPT
    from app.agent.nodes.conversation import CONVERSATION_SYSTEM_PROMPT
    from app.agent.nodes.human_handover import HANDOVER_PROMPT
    from app.agent.nodes.knowledge import KNOWLEDGE_SYSTEM_PROMPT
    conversation = CONVERSATION_SYSTEM_PROMPT.format(pronoun_rule="", hotline="", style="")
    expected = {"knowledge": KNOWLEDGE_SYSTEM_PROMPT, "conversation": conversation, "handover": HANDOVER_PROMPT,
                "summary": SUMMARY_PROMPT, "contract": CONTRACT_SYSTEM_PROMPT}
    assert set(expected) == set(TEXT_ROLE_MARKERS)
    for role, prompt in expected.items():
        assert role_of_system(prompt) == role, f"cụm nhận diện của vai {role!r} không còn khớp prompt thật"


def test_tool_roles_match_real_tool_sets():
    from app.agent.nodes import appointment, compose, policy_guard, sales
    assert role_of_tools(appointment.TOOLS) == "appointment"
    assert role_of_tools(policy_guard.POLICY_TOOLS) == "policy"
    assert role_of_tools(compose.TOOLS) == "compose"
    assert role_of_tools(sales.PRODUCT_SEARCH_TOOLS) == "sales"


# ---- generate() ----
def test_generate_defaults_to_empty_text_so_nodes_use_their_fallback(fake_llm):
    from app.agent.nodes.knowledge import KNOWLEDGE_SYSTEM_PROMPT
    assert llm.generate(KNOWLEDGE_SYSTEM_PROMPT, "câu hỏi") == ""


def test_generate_returns_scripted_text_per_role(fake_llm):
    from app.agent.nodes.knowledge import KNOWLEDGE_SYSTEM_PROMPT
    fake_llm.says("knowledge", "Thay dầu mỗi 10.000 km.")
    assert llm.generate(KNOWLEDGE_SYSTEM_PROMPT, "câu hỏi") == "Thay dầu mỗi 10.000 km."


# ---- generate_structured() ----
def test_structured_interpretation_is_looked_up_by_user_text(fake_llm):
    from app.contracts.schemas import ConversationInterpretation
    fake_llm.understands("Xin chào", task("GREETING", "greeting", "Xin chào"))
    got = llm.generate_structured("sys", "  xin   chào ", ConversationInterpretation)  # chuẩn hóa hoa/thường, khoảng trắng
    assert got is not None and got.tasks[0].action == "greeting"


def test_unscripted_message_returns_none_and_fails_the_test_at_teardown():
    from app.contracts.schemas import ConversationInterpretation
    fake = FakeLLM()
    assert fake.with_structured_output(ConversationInterpretation).invoke([]) is None  # không có tin khách nào
    with pytest.raises(AssertionError, match="không có kịch bản"):
        fake.assert_clean()


# ---- date_extraction đi qua fake: phép tính ngày vẫn là code thật ----
@pytest.mark.parametrize("words, day, hhmm", [
    ("ngày mai lúc 10 giờ sáng", "2026-11-20", "10:00"),
    ("sáng thứ 7 tuần này, 8 giờ", "2026-11-21", "08:00"),
    ("thứ 7 tuần sau lúc 14h30", "2026-11-28", "14:30"),
    ("ngày 15/12 lúc 2 giờ chiều", "2026-12-15", "14:00"),
])
def test_date_extraction_resolves_through_fake(fake_llm, words, day, hhmm):
    got = extract_date(words, TODAY)
    assert (got["status"], got["date"], got["time"]) == ("RESOLVED", day, hhmm)


# ---- generate_with_tools(): vòng lặp công cụ thật, kịch bản giả ----
def test_tool_loop_runs_scripted_rounds_and_feeds_results_back(fake_llm):
    from app.agent.nodes import appointment
    executed = []

    def executor(name, args):
        executed.append((name, args))
        return {"appointments": [{"id": 41}]} if name == "get_appointment_details" else {"ok": True}

    fake_llm.uses_tools("appointment", [
        [("get_appointment_details", {})],
        [("cancel_appointment", lambda results: {"appointment_id": results[-1]["appointments"][0]["id"]})],
    ], final="Đã hủy.")
    out = llm.generate_with_tools("sys", "Tin nhắn của khách: hủy lịch", appointment.TOOLS, executor)
    assert [n for n, _ in executed] == ["get_appointment_details", "cancel_appointment"]
    assert executed[1][1] == {"appointment_id": 41}
    assert out["final_response"] == "Đã hủy." and out["error"] is None
    assert fake_llm.tool_names("appointment") == ["get_appointment_details", "cancel_appointment"]


def test_tool_script_can_be_limited_to_one_message(fake_llm):
    from app.agent.nodes import appointment
    fake_llm.uses_tools("appointment", [[("get_appointment_details", {})]], when="lịch của tôi")
    out = llm.generate_with_tools("sys", "Tin nhắn của khách: đặt lịch mới", appointment.TOOLS, lambda n, a: {})
    assert out["tool_calls"][0]["name"] == "create_appointment"  # mặc định của vai appointment
    out = llm.generate_with_tools("sys", "Tin nhắn của khách: lịch của tôi", appointment.TOOLS, lambda n, a: {})
    assert out["tool_calls"][0]["name"] == "get_appointment_details"


def test_fake_rejects_a_tool_the_node_does_not_offer(fake_llm):
    from app.agent.nodes import appointment
    fake_llm.uses_tools("appointment", [[("allow_bot", {})]])
    out = llm.generate_with_tools("sys", "x", appointment.TOOLS, lambda n, a: {})
    assert out["error"] and "allow_bot" in out["error"]  # llm.py bắt AssertionError và trả error


def test_tool_call_spy_records_executed_tools_per_node(fake_llm, tool_call_spy):
    from app.agent.nodes import policy_guard
    fake_llm.uses_tools("policy", [[("handover_to_human", {"reason": "COMPLAINT"}), ("flag_safety_risk", {})]])
    result = policy_guard.generate_with_tools(policy_guard.POLICY_PROMPT, "x", policy_guard.POLICY_TOOLS,
                                              lambda name, args: {"ok": True})
    assert result["error"] is None
    assert [(r.node, r.name) for r in tool_call_spy] == [("policy_guard", "handover_to_human"),
                                                         ("policy_guard", "flag_safety_risk")]
