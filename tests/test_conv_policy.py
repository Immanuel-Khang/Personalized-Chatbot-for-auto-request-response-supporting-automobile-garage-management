"""Chính sách hội thoại: trả lời trước, gợi ý bước tiếp theo nhưng không tự thực hiện, chỉ hỏi thông tin cần thiết.
Chạy rule-based (LLM_PROVIDER=none) qua /chat + graph thật; assert trên state/trace/DB, không so khớp nguyên văn."""
from unittest.mock import Mock

from app.agent.nodes.preprocess import extract_when
from app.agent.nodes.router import router
from app.services import get_services
from tests.conversation_helpers import (BOOKED, BOOKING_SLOTS, DATE_ASK, NOT_UNDERSTOOD, PHONE_ASK, Conversation,
                                        mentions, unique_phone)

TC01 = "Xe tôi đang gặp vấn đề với kính chắn gió, có thể mang qua hãng để sửa được không?"


# ---- Gợi ý ≠ thực hiện: hỏi dịch vụ chỉ để lại offer, không mở flow ----
def test_service_inquiry_leaves_offer_not_flow(client):
    conv = Conversation(client, "offer")
    result = conv.say(TC01)
    state = conv.state
    assert "conversation" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert state["offer"]["intent"] == "APPOINTMENT" and state.get("active_flow") is None
    assert not mentions(result["reply"], PHONE_ASK) and conv.appointments() == []


def test_accepting_offer_with_time_enters_booking_without_creating(client):
    conv = Conversation(client, "offer-accept")
    conv.say(TC01)
    result = conv.say("Ngày mai lúc 10 giờ")
    state = conv.state
    assert "appointment" in result["trace"] and state["active_flow"] == "appointment"
    assert state["slots"]["service_type"] == "REPAIR" and "10 giờ" in state["slots"]["when_text"]
    assert mentions(result["reply"], PHONE_ASK) and not mentions(result["reply"], DATE_ASK)
    assert conv.appointments() == [] and state.get("offer") is None


def test_known_phone_offer_acceptance_asks_confirmation_before_booking(client):
    conv = Conversation(client, "offer-confirm")
    conv.say(f"Số điện thoại của mình là {unique_phone()}")
    conv.say(TC01)
    tentative = conv.say("Ngày mai lúc 10 giờ")
    assert BOOKED not in tentative["reply"] and conv.appointments() == []  # giờ dự kiến chưa phải đồng ý đặt
    confirmed = conv.say("Được")
    assert BOOKED in confirmed["reply"] and len(conv.appointments()) == 1


def test_ignored_offer_expires_and_keeps_no_booking_slot(client):
    conv = Conversation(client, "offer-ignore")
    conv.say(TC01)
    result = conv.say("Bao lâu thì nên thay dầu động cơ?")
    state = conv.state
    assert "appointment" not in result["trace"] and state.get("offer") is None
    assert not BOOKING_SLOTS & set(state["slots"]) and conv.appointments() == []
    later = conv.say("Được")  # offer đã hết hiệu lực
    assert "appointment" not in later["trace"]


# ---- TC14: chỉ muốn hỏi giá, chưa đặt lịch ----
def test_tc14_price_only_does_not_start_booking(client):
    conv = Conversation(client, "tc14")
    result = conv.say("Tôi chỉ muốn hỏi giá trước thôi, chưa định đặt lịch.")
    assert "appointment" not in result["trace"] and conv.state.get("active_flow") is None
    assert not mentions(result["reply"], DATE_ASK + PHONE_ASK) and conv.appointments() == []


# ---- TC15: đổi hạng mục giữa lúc đặt lịch ----
def test_tc15_switching_service_updates_context_keeps_identity(client):
    conv = Conversation(client, "tc15")
    customer = conv.link_customer(unique_phone())
    conv.say("Tôi muốn đặt lịch sửa kính chắn gió ngày mai.")
    assert conv.state["slots"]["service_detail"] == "kính chắn gió"
    result = conv.say("Thôi, tôi muốn kiểm tra điều hòa trước.")
    state = conv.state
    assert "appointment" in result["trace"] and state["active_flow"] == "appointment"
    assert state["slots"]["service_detail"] == "điều hòa" and "đã dừng" not in result["reply"]
    assert conv.customer_id() == customer


# ---- TC16: hệ thống đặt lịch lỗi ----
def test_tc16_booking_error_is_not_reported_as_booked(client, monkeypatch):
    monkeypatch.setattr(get_services().booking, "create_appointment", Mock(side_effect=RuntimeError("db down")))
    conv = Conversation(client, "tc16")
    conv.say(f"Số điện thoại của mình là {unique_phone()}")
    result = conv.say("Tôi muốn đặt lịch bảo dưỡng lúc 2 giờ chiều mai.")
    state = conv.state
    assert BOOKED not in result["reply"] and "chưa xác nhận được" in result["reply"]
    assert "hotline" in result["reply"] and "db down" not in result["reply"]  # không lộ lỗi nội bộ
    assert state["active_flow"] == "appointment" and state["slots"].get("when_text")  # giữ slot để thử lại
    assert conv.appointments() == []


# ---- TC12: câu hỏi giá giữa lúc đặt lịch không lấp slot ----
def test_tc12_price_interrupt_keeps_pending_booking(client):
    conv = Conversation(client, "tc12-policy")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai.")
    result = conv.say("Nhân tiện, thay dầu cho Foton Aumark hết bao nhiêu tiền?")
    state = conv.state
    assert "appointment" not in result["trace"] and state["active_flow"] == "appointment"
    assert state["slots"]["when_text"] == "ngày mai"


# ---- Đơn vị: giờ hẹn, gợi ý chỉ sống 1 lượt ----
def test_extract_when_merges_hour_with_previous_day():
    assert extract_when("ngày mai lúc 10 giờ sáng") == "ngày mai 10 giờ sáng"
    assert extract_when("Khoảng 10 giờ sáng", previous="ngày mai") == "ngày mai 10 giờ sáng"
    assert extract_when("2h30 chiều thứ 7") == "thứ 7 2 giờ 30 chiều"
    assert extract_when("Kính bị nứt") is None


def test_router_always_clears_offer():
    offer = {"intent": "APPOINTMENT", "text": TC01}
    assert router({"intents": ["OTHER"], "user_text": "Bạn thích ăn gì", "offer": offer})["offer"] is None


# ---- TC09: xin giảm 15% khi mua xe → chuyển người duyệt, không hứa ----
def test_tc09_purchase_discount_goes_to_manager(client):
    conv = Conversation(client, "tc09-buy")
    result = conv.say("Nếu mua xe thì bên mình giảm giá cho tôi 15% được không?")
    assert result["mode"] == "HUMAN_PENDING" and "human_handover" in result["trace"]
    assert "giảm 15%" not in result["reply"] and not {"sales", "contract"} & set(result["trace"])


# ---- TC06: câu trả lời ngắn "Khoảng 10 giờ sáng" lấp đúng slot giờ ----
def test_tc06_short_time_reply_fills_slot(client):
    conv = Conversation(client, "tc06-short")
    conv.say("Tôi muốn đặt lịch bảo dưỡng vào ngày mai.")
    result = conv.say("Khoảng 10 giờ sáng.")
    assert "appointment" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert conv.state["slots"]["when_text"] == "ngày mai 10 giờ sáng" and conv.appointments() == []
