"""Nhóm D (TC11-TC13) + bất biến chung: ngữ cảnh nhiều lượt, ngắt flow đặt lịch, dịch vụ chưa rõ khả năng."""
import re

import pytest

from tests.conversation_helpers import (BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation, defect,
                                        mentions, questions, unsupported_amounts)

TC11 = ["Xe tôi có tiếng kêu lạ khi phanh.", "Chỉ khi đạp phanh thôi."]
TC12 = ["Tôi muốn đặt lịch bảo dưỡng xe.", "À mà thay dầu xe Foton Aumark hết bao nhiêu tiền?"]  # Vios -> Aumark
TC13 = "Gara bên bạn có sửa hệ thống pin của xe điện bị lỗi không?"
CAPABILITY_CLAIMS = ("có sửa", "sửa được", "nhận sửa", "có thể sửa", "có hỗ trợ sửa")
UNAVAILABLE_CLAIMS = ("không sửa", "không nhận", "không hỗ trợ", "không có dịch vụ")
def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def _workflow(state):
    workflow = state.get("active_workflow")
    return workflow.workflow_type if workflow else None


# ---- TC11: câu trả lời ngắn phụ thuộc ngữ cảnh ----
def test_tc11_short_reply_stays_in_brake_topic(client):
    conv = Conversation(client, "tc11")
    first = conv.say(TC11[0])
    second = conv.say(TC11[1])
    for result in (first, second):
        assert "knowledge" in result["trace"] and "appointment" not in result["trace"]
        assert not mentions(result["reply"], DIAGNOSIS_CLAIMS)
    assert "phanh" in second["reply"].lower() and "kiểm tra" in second["reply"].lower()  # khuyên đi kiểm tra
    assert _workflow(conv.state) is None and conv.appointments() == []
    assert "đã dừng" not in second["reply"]  # 'thôi' không bị hiểu là hủy


@defect("DEFECT-11", "knowledge không dùng lịch sử hội thoại: lượt 2 lặp lại nguyên văn câu trả lời lượt 1")
def test_tc11_follow_up_is_not_a_verbatim_repeat(client):
    conv = Conversation(client, "tc11-repeat")
    first = conv.say(TC11[0])
    assert conv.say(TC11[1])["reply"] != first["reply"]


# ---- TC12: hỏi giá giữa lúc đặt lịch ----
def test_tc12_price_question_pauses_and_resumes_booking(client):
    conv = Conversation(client, "tc12")
    first = conv.say(TC12[0])
    assert mentions(first["reply"], DATE_ASK) and _workflow(conv.state) == "appointment"
    second = conv.say(TC12[1])
    state = conv.state
    assert "appointment" not in second["trace"] and not mentions(second["reply"], DATE_ASK)  # không ép trả lời ngày
    assert _workflow(state) == "appointment" and _fact(state, "service_type") == "MAINTENANCE"
    assert not {"appointment_when"} & set(state.get("known_facts", {}))  # câu hỏi giá không lấp slot ngày
    assert "đã dừng" not in second["reply"] and unsupported_amounts(second["reply"]) == []
    resumed = conv.say("Sáng thứ 7")
    assert "appointment" in resumed["trace"] and "thứ 7" in _fact(conv.state, "appointment_when")
    assert mentions(resumed["reply"], PHONE_ASK) and conv.appointments() == []


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


def test_tc13_says_availability_needs_verification(client):
    reply = Conversation(client, "tc13-verify").say(TC13)["reply"].lower()
    assert NOT_UNDERSTOOD not in reply and mentions(reply, ("kiểm tra", "xác nhận", "hotline", "nhân viên"))


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


def test_conversations_are_isolated(client):
    booking, other = Conversation(client, "iso-a"), Conversation(client, "iso-b")
    booking.say(TC12[0])
    result = other.say("Sáng thứ 7")  # phiên khác không đang đặt lịch
    assert "appointment" not in result["trace"] and _workflow(other.state) is None
    assert _workflow(booking.state) == "appointment"
