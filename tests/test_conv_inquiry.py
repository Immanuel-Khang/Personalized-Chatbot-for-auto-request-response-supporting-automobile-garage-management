"""Nhóm A (TC01-TC03): hỏi dịch vụ/giá không tự mở đặt lịch, trả lời đúng trọng tâm, không bịa năng lực/giá.
Catalog và data/knowledge/ hiện không có dịch vụ kính chắn gió -> bot không được khẳng định là nhận sửa/báo giá."""
from tests.conversation_helpers import (AMOUNT, BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation,
                                        defect, mentions, questions, unsupported_amounts)

TC01 = "Xe tôi đang gặp vấn đề với kính chắn gió, có thể mang qua hãng để sửa được không?"
TC02 = "Kính chắn gió xe tôi có một vết nứt nhỏ ở góc bên phải."
TC03 = "Thay kính chắn gió hết bao nhiêu tiền vậy?"
CAPABILITY_CLAIMS = ("có sửa", "sửa được", "nhận sửa", "có thể sửa", "có hỗ trợ sửa", "có thay kính")
DIAGNOSIS_CLAIMS = ("chắc chắn", "nguyên nhân là", "chắc là do")
VERIFY = ("kiểm tra", "xác nhận", "hotline", "nhân viên")


def assert_no_booking(conv: Conversation, result: dict) -> None:
    """Câu hỏi không phải yêu cầu đặt lịch: không vào flow, không tạo lịch, không xin slot, tối đa 1 câu hỏi."""
    state = conv.state
    assert "appointment" not in result["trace"]
    assert state.get("active_flow") is None and not state.get("pending_intent")
    assert conv.appointments() == []
    assert not mentions(result["reply"], DATE_ASK + PHONE_ASK)
    assert questions(result["reply"]) <= 1


# ---- TC01: hỏi có sửa kính chắn gió không ----
def test_tc01_service_inquiry_does_not_start_booking(client):
    conv = Conversation(client, "tc01")
    result = conv.say(TC01)
    assert_no_booking(conv, result)
    assert not mentions(result["reply"], CAPABILITY_CLAIMS)
    assert unsupported_amounts(result["reply"]) == []


def test_tc01_answers_the_service_question(client):
    reply = Conversation(client, "tc01-answer").say(TC01)["reply"]
    assert NOT_UNDERSTOOD not in reply.lower()
    assert "kính" in reply.lower() and mentions(reply, VERIFY)


def test_tc01_inquiry_collects_no_booking_slot(client):
    conv = Conversation(client, "tc01-slots")
    conv.say(TC01)
    assert not BOOKING_SLOTS & set(conv.state.get("slots", {}))


# ---- TC02: mô tả hư hỏng, không đòi đặt lịch ----
def test_tc02_damage_description_does_not_start_booking(client):
    conv = Conversation(client, "tc02")
    result = conv.say(TC02)
    assert_no_booking(conv, result)
    assert not mentions(result["reply"], DIAGNOSIS_CLAIMS)


def test_tc02_acknowledges_the_damage(client):
    reply = Conversation(client, "tc02-ack").say(TC02)["reply"]
    assert NOT_UNDERSTOOD not in reply.lower()
    assert mentions(reply, ("kính", "vết nứt"))


# ---- TC03: hỏi giá thay kính ----
def test_tc03_price_question_fabricates_no_price_and_no_booking(client):
    conv = Conversation(client, "tc03")
    result = conv.say(TC03)
    assert_no_booking(conv, result)
    assert unsupported_amounts(result["reply"]) == []


@defect("DEFECT-03", "ProductService.search ILIKE theo từng từ ('gió') -> báo giá lọc gió thay cho giá kính")
def test_tc03_unknown_price_is_flagged_not_substituted(client):
    reply = Conversation(client, "tc03-price").say(TC03)["reply"]
    assert AMOUNT.findall(reply) == []  # catalog không có giá kính -> không báo giá món khác
    assert mentions(reply, VERIFY + ("dòng xe", "mẫu xe", "loại kính"))
