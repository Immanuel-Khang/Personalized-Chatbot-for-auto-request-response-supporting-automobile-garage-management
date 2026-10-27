"""Nhận diện service_type theo luật: chắc chắn → tự điền, mơ hồ → hỏi lại đúng các lựa chọn."""
import pytest

from app.agent.nodes.service_type import clarify_question, detect_service_type, resolve_service_type
from app.contracts.schemas import ServiceType as S


@pytest.mark.parametrize("text, expected", [
    ("Tôi muốn qua xưởng xem xe", S.SHOWROOM_VISIT),
    ("Mình muốn đến showroom xem xe Foton", S.SHOWROOM_VISIT),
    ("Cho mình xem xe trực tiếp", S.SHOWROOM_VISIT),
    ("Cho mình chạy thử", S.TEST_DRIVE),
    ("Muốn đến showroom lái thử", S.TEST_DRIVE),  # PRECEDENCE: lái thử cụ thể hơn
    ("Đặt lịch bảo dưỡng", S.MAINTENANCE),
    ("Xe bị hỏng phanh", S.REPAIR),
    ("Qua xưởng xem xe bị kêu", S.REPAIR),  # mang xe đến sửa, không phải xem xe mới
    ("TEST_DRIVE", S.TEST_DRIVE),  # giá trị enum từ LLM
])
def test_detects_clear_service(text, expected):
    assert detect_service_type(text).service == expected


@pytest.mark.parametrize("text", ["Mình muốn đặt lịch", "xem xe có bị gì không", "", None])
def test_unclear_text_has_no_service(text):
    result = detect_service_type(text)
    assert result.service is None and not result.ambiguous


def test_conflicting_services_ask_back_with_only_those_options():
    result = detect_service_type("Bảo dưỡng và sửa luôn")
    assert result.ambiguous and set(result.candidates) == {S.REPAIR, S.MAINTENANCE}
    question = clarify_question(result.candidates)
    assert "sửa chữa" in question and "bảo dưỡng" in question and "lái thử" not in question


def test_resolve_prefers_rule_then_existing_slot():
    assert resolve_service_type("Cho mình lái thử", "MAINTENANCE").service == S.TEST_DRIVE  # khách đổi ý
    assert resolve_service_type("Ngày mai", "REPAIR").service == S.REPAIR  # giữ slot lượt trước
    assert resolve_service_type("Bảo dưỡng và sửa luôn", "REPAIR").service == S.REPAIR  # LLM gỡ mơ hồ
    assert resolve_service_type("Ngày mai", "garbage").service is None  # giá trị lạ → hỏi lại
