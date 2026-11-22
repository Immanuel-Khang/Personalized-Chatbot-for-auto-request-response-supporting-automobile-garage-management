"""Nhóm D (TC11-TC13) + bất biến chung: ngữ cảnh nhiều lượt, ngắt flow đặt lịch, dịch vụ chưa rõ khả năng."""
import re

import pytest

from tests.conversation_helpers import (BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation, defect,
                                        mentions, questions, unsupported_amounts)
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

TC11 = ["Xe tôi có tiếng kêu lạ khi phanh.", "Chỉ khi đạp phanh thôi."]
TC12 = ["Tôi muốn đặt lịch bảo dưỡng xe.", "À mà thay dầu xe Foton Aumark hết bao nhiêu tiền?"]  # Vios -> Aumark
TC13 = "Gara bên bạn có sửa hệ thống pin của xe điện bị lỗi không?"
CAPABILITY_CLAIMS = ("có sửa", "sửa được", "nhận sửa", "có thể sửa", "có hỗ trợ sửa")
UNAVAILABLE_CLAIMS = ("không sửa", "không nhận", "không hỗ trợ", "không có dịch vụ")
VERIFY = ("kiểm tra", "xác nhận", "hotline", "nhân viên")


def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def _workflow(state):
    workflow = state.get("active_workflow")
    return workflow.workflow_type if workflow else None


# ---- TC11: câu trả lời ngắn phụ thuộc ngữ cảnh ----
def test_tc11_short_reply_stays_in_brake_topic(client, fake_llm):
    """FakeLLM đã 'hiểu' cả hai lượt là KNOWLEDGE (việc hiểu đúng câu ngắn 'Chỉ khi đạp phanh thôi' thuộc L5);
    ở đây kiểm code: không mở đặt lịch, không coi 'thôi' là hủy, không để lại workflow/lịch."""
    fake_llm.understands(TC11[0], task("KNOWLEDGE", "answer_knowledge", "tiếng kêu lạ khi phanh"))
    fake_llm.understands(TC11[1], task("KNOWLEDGE", "answer_knowledge", "chỉ khi đạp phanh",
                                       relationship="ANSWER_TO_QUESTION"))
    fake_llm.says("knowledge", "Phanh kêu thường do má phanh mòn; bạn nên kiểm tra phanh sớm.")
    conv = Conversation(client, "tc11")
    first = conv.say(TC11[0])
    second = conv.say(TC11[1])
    for result in (first, second):
        assert "knowledge" in result["trace"] and "appointment" not in result["trace"]
    assert "đã dừng" not in second["reply"]
    assert _workflow(conv.state) is None and conv.appointments() == []


@defect("DEFECT-11", "knowledge không dùng lịch sử hội thoại: lượt 2 lặp lại nguyên văn câu trả lời lượt 1")
def test_tc11_follow_up_is_not_a_verbatim_repeat(client):
    conv = Conversation(client, "tc11-repeat")
    first = conv.say(TC11[0])
    assert conv.say(TC11[1])["reply"] != first["reply"]


# ---- TC12: hỏi giá giữa lúc đặt lịch ----
def test_tc12_price_question_pauses_and_resumes_booking(client, fake_llm):
    fake_llm.understands(TC12[0], task("APPOINTMENT", "book_appointment", "đặt lịch bảo dưỡng"),
                         slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng")])
    fake_llm.understands(TC12[1], task("SALES", "ask_price", "thay dầu Foton Aumark"))
    fake_llm.uses_tools("sales", [], final="Dạ mình sẽ gửi bảng giá thay dầu cho bạn ạ.")
    fake_llm.understands("Sáng thứ 7", task("APPOINTMENT", "book_appointment", "Sáng thứ 7", "ANSWER_TO_QUESTION"),
                         slots=[slot("appointment_when", "sáng thứ 7", "Sáng thứ 7")])
    conv = Conversation(client, "tc12")
    first = conv.say(TC12[0])
    assert mentions(first["reply"], DATE_ASK) and _workflow(conv.state) == "appointment"
    second = conv.client.post("/chat", json={"session_id": conv.id, "message": TC12[1]}).json()
    state = conv.state
    assert "sales" in second["trace"] and "appointment" not in second["trace"]
    assert not mentions(second["reply"], DATE_ASK)                            # không ép trả lời ngày
    assert _workflow(state) == "appointment" and _fact(state, "service_type") == "MAINTENANCE"
    assert not {"appointment_when"} & set(state.get("known_facts", {}))      # câu hỏi giá không lấp slot ngày
    assert "đã dừng" not in second["reply"]
    resumed = conv.say("Sáng thứ 7")
    assert "appointment" in resumed["trace"] and "thứ 7" in _fact(conv.state, "appointment_when")
    assert conv.appointments() == []


@defect("DEFECT-12", "sales trả giá xe tải nguyên chiếc (≥ 9 chữ số) cho câu hỏi giá thay dầu")
def test_tc12_oil_change_price_is_not_a_vehicle_price(client):
    conv = Conversation(client, "tc12-price")
    conv.say(TC12[0])
    reply = conv.say(TC12[1])["reply"]
    assert not any(len(a.replace(".", "")) >= 9 for a in re.findall(r"\d[\d.]*(?=\s*đồng)", reply))


# ---- TC13: dịch vụ chưa rõ có hỗ trợ không ----
def test_tc13_unknown_service_makes_no_claim_either_way(client):
    conv = Conversation(client, "tc13")
    result = conv.say(TC13)
    assert "appointment" not in result["trace"] and conv.state.get("active_workflow") is None
    assert conv.appointments() == [] and result["mode"] == "BOT"
    # Không có dữ liệu năng lực -> không được khẳng định có, cũng không được khẳng định không
    assert not mentions(result["reply"], CAPABILITY_CLAIMS + UNAVAILABLE_CLAIMS)
    assert not mentions(result["reply"], DATE_ASK + PHONE_ASK) and questions(result["reply"]) <= 1


def test_tc13_says_availability_needs_verification(client, fake_llm):
    """LLM im lặng -> node dùng câu dự phòng xác định: phải nói cần xác minh, không nói 'chưa hiểu'."""
    fake_llm.understands(TC13, task("KNOWLEDGE", "answer_knowledge", "sửa pin xe điện", inquiry_only=True))
    reply = Conversation(client, "tc13-verify").say(TC13)["reply"].lower()
    assert NOT_UNDERSTOOD not in reply and mentions(reply, VERIFY)


def test_tc13_collects_no_booking_slot(client):
    conv = Conversation(client, "tc13-slots")
    conv.say(TC13)
    assert not BOOKING_SLOTS & set(conv.state.get("known_facts", {}))


# ---- Bất biến chung: câu hỏi không xin SĐT/ngày giờ, tối đa một câu hỏi ----
INQUIRIES = [
    "Xe tôi đang gặp vấn đề với kính chắn gió, có thể mang qua hãng để sửa được không?",
    "Kính chắn gió xe tôi có một vết nứt nhỏ ở góc bên phải.",
    "Thay kính chắn gió hết bao nhiêu tiền vậy?",
    "Tôi đang muốn tìm một chiếc Foton Aumark để đi làm hằng ngày.",
    "Tôi muốn ký hợp đồng mua xe, cần chuẩn bị giấy tờ gì?",
    "Xe tôi có tiếng kêu lạ khi phanh.",
    "Gara bên bạn có sửa hệ thống pin của xe điện bị lỗi không?",
]


@pytest.mark.parametrize("text", INQUIRIES)
def test_inquiries_collect_no_personal_or_booking_data(client, text):
    conv = Conversation(client, "inquiry")
    result = conv.say(text)
    assert not mentions(result["reply"], DATE_ASK + PHONE_ASK) and questions(result["reply"]) <= 1
    assert _workflow(conv.state) is None and conv.appointments() == []


def test_conversations_are_isolated(client, fake_llm):
    fake_llm.understands(TC12[0], task("APPOINTMENT", "book_appointment", "đặt lịch bảo dưỡng"),
                         slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng")])
    fake_llm.understands("Sáng thứ 7", task("OTHER", "general_conversation", "Sáng thứ 7"))  # phiên khác: không có ngữ cảnh
    booking, other = Conversation(client, "iso-a"), Conversation(client, "iso-b")
    booking.say(TC12[0])
    result = other.say("Sáng thứ 7")
    assert "appointment" not in result["trace"] and _workflow(other.state) is None
    assert _workflow(booking.state) == "appointment"


