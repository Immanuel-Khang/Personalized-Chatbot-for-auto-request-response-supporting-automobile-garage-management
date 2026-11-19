"""L1: output_guard.violation (không DB, không LLM). Bảng OG01-OG08 trong kế hoạch B8.
Test thường = hành vi hiện tại đúng. @defect (xfail strict) = giới hạn ĐÃ BIẾT: assertion mô tả hành vi MONG MUỐN,
hiện đang fail; khi ai đó sửa guard, test báo XPASS và buộc bạn gỡ @defect."""
import pytest

from app.agent.nodes.output_guard import violation
from tests.conversation_helpers import defect

ALLOWED = {"425.000.000 đồng"}


# ---- OG04, OG05 + trường hợp bình thường: guard làm đúng việc ----
def test_og04_wrong_full_amount_is_blocked():
    assert "số tiền lạ" in violation("Giá xe là 399.000.000 đồng", ALLOWED)


def test_allowed_amount_passes_whatever_unit_suffix():
    assert violation("Giá xe là 425.000.000 đồng", ALLOWED) is None
    assert violation("Giá xe là 425.000.000 VNĐ", ALLOWED) is None  # so theo giá trị số, không theo đuôi


def test_og05_percent_discount_next_to_giam_is_blocked():
    assert "cam kết/giảm giá" in violation("Mình giảm 10% cho bạn nhé", set())


@pytest.mark.parametrize("phrase", ["Mình đảm bảo giá này", "Xưởng cam kết hoàn tiền", "Mình tặng bạn một bộ thảm"])
def test_commitment_words_are_blocked(phrase):
    assert violation(phrase, ALLOWED)


def test_plain_reply_passes():
    assert violation("Bạn cần bảo dưỡng xe vào thời gian nào ạ?", ALLOWED) is None


def test_nine_digit_boundary_with_allowed_amounts():
    # 8 chữ số (< 9) không bị kiểm: đây là ranh giới hiện tại, xem OG02
    assert violation("Phụ phí 15.000.000 đồng", ALLOWED) is None
    assert violation("Phụ phí 150.000.000 đồng", ALLOWED) is not None


# ---- Giới hạn đã biết (đặt tên theo kế hoạch) ----
@defect("OG01", "số tiền viết tắt ('800 triệu') không được kiểm vì chỉ xét từ 9 chữ số")
def test_og01_abbreviated_price_outside_allowed_is_blocked():
    assert violation("Xe này giá 800 triệu", ALLOWED)


@defect("OG02", "khoản nhỏ (< 9 chữ số) không có trong dữ liệu vẫn lọt")
def test_og02_small_amount_not_in_allowed_is_blocked():
    assert violation("Phí dịch vụ là 2.500.000 đồng", ALLOWED)


@defect("OG03", "'miễn phí'/'tặng' nằm trong FORBIDDEN nên câu từ chối hợp lệ cũng bị chặn nhầm")
def test_og03_honest_refusal_mentioning_free_is_not_blocked():
    assert violation("Xưởng không có chương trình miễn phí hay tặng quà kèm theo ạ", ALLOWED) is None


@defect("OG06", "allowed_amounts rỗng thì guard bỏ qua mọi số tiền: bịa giá không qua công cụ vẫn lọt")
def test_og06_full_amount_with_empty_allowed_is_blocked():
    assert violation("Giá xe là 425.000.000 đồng", set())


@pytest.mark.parametrize("written", ["399.000.000đ", "399.000.000 VND", "399.000.000 vnd"])
@defect("OG07", "regex chỉ bắt đuôi đồng/triệu/tỷ/vnđ; 'đ' và 'VND' lọt")
def test_og07_other_currency_spellings_are_blocked(written):
    assert violation(f"Giá xe là {written}", ALLOWED)


@pytest.mark.parametrize("phrase", ["Mình giảm giá 10% cho bạn", "Giảm 5 triệu cho bạn", "Bớt 10 triệu nhé",
                                    "Đang có khuyến mãi tháng này"])
@defect("OG08", "FORBIDDEN cần 'giảm' sát chữ số kèm %; thiếu 'bớt', 'khuyến mãi'; '5 triệu' dưới 9 chữ số")
def test_og08_discount_phrasings_are_blocked(phrase):
    assert violation(phrase, ALLOWED)
