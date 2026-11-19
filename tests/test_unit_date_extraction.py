"""L1: phần CODE của date_extraction (phép tính ngày/giờ, các chốt chặn xác định). Ngày tham chiếu truyền thẳng vào
hàm nên không cần freeze_time. Phần LLM đọc thành phần ngày được thay bằng DateInfo dựng tay: test kiểm phép tính,
không kiểm LLM đọc đúng (việc đó thuộc L5). Hôm nay giả định = 2026-11-19 (thứ 5); tuần này: 16/11 (T2) - 22/11 (CN)."""
from datetime import date

import pytest

from app.agent import date_extraction as de
from app.agent.date_extraction import DateInfo, Kind, Period, Weekday

TODAY = date(2026, 11, 19)


# ---- resolve(): thành phần -> ngày ----
@pytest.mark.parametrize("info, expected", [
    (DateInfo(kind=Kind.RELATIVE, offset=0), date(2026, 11, 19)),
    (DateInfo(kind=Kind.RELATIVE, offset=1), date(2026, 11, 20)),
    (DateInfo(kind=Kind.RELATIVE, offset=62), date(2027, 1, 20)),
    (DateInfo(kind=Kind.RELATIVE, offset=63), None),        # ngoài giới hạn 62 ngày
    (DateInfo(kind=Kind.RELATIVE, offset=-1), None),        # quá khứ
    (DateInfo(kind=Kind.RELATIVE), None),                   # thiếu offset
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_7, week_offset=0), date(2026, 11, 21)),
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_7, week_offset=1), date(2026, 11, 28)),
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.CHU_NHAT, week_offset=0), date(2026, 11, 22)),  # CN thuộc tuần này
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_2, week_offset=0), date(2026, 11, 16)),     # quá khứ: tính ra được, bị chặn ở extract_date
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_7, week_offset=3), None),                    # chỉ nhận 0, 1, 2
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_5), date(2026, 11, 19)),                     # không nói tuần: ngày gần nhất, kể cả hôm nay
    (DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_2), date(2026, 11, 23)),                     # thứ 2 sắp tới
    (DateInfo(kind=Kind.WEEKDAY), None),
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month=12), date(2026, 12, 15)),
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month=1), date(2027, 1, 15)),   # tháng đã qua -> năm sau
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month=12, year=2027), date(2027, 12, 15)),
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month_offset=1), date(2026, 12, 15)),
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month_offset=0), date(2026, 11, 15)),
    (DateInfo(kind=Kind.MONTH_DAY, day=15, month=12, month_offset=1), None),  # vừa month vừa month_offset
    (DateInfo(kind=Kind.MONTH_DAY, day=31, month=2), None),                   # ngày không tồn tại
    (DateInfo(kind=Kind.MONTH_DAY, day=15), None),                            # thiếu tháng
    (DateInfo(kind=Kind.MONTH_DAY, day=0, month=12), None),
    (DateInfo(kind=Kind.UNKNOWN), None),
])
def test_resolve(info, expected):
    assert de.resolve(info, TODAY) == expected


# ---- resolve_time(): giờ nói -> HH:MM ----
@pytest.mark.parametrize("hour, minute, period, expected", [
    (None, None, None, (de.MISSING, None)),
    (10, None, Period.AM, (de.RESOLVED, "10:00")),
    (8, 30, Period.AM, (de.RESOLVED, "08:30")),
    (2, None, Period.PM, (de.RESOLVED, "14:00")),
    (14, 30, None, (de.RESOLVED, "14:30")),       # giờ 13-23 không cần buổi
    (12, None, None, (de.RESOLVED, "12:00")),     # trưa
    (0, None, None, (de.RESOLVED, "00:00")),
    (8, None, None, (de.NEEDS_PERIOD, None)),     # 8 giờ sáng hay tối?
    (24, None, Period.AM, (de.MISSING, None)),
    (10, 60, Period.AM, (de.MISSING, None)),
])
def test_resolve_time(hour, minute, period, expected):
    assert de.resolve_time(DateInfo(kind=Kind.UNKNOWN, hour=hour, minute=minute, period=period)) == expected


# ---- chốt chặn xác định (chạy trước khi hỏi LLM) ----
@pytest.mark.parametrize("text, expected", [
    ("ngày mai", True), ("thứ 7 này", True), ("14/11", True), ("chủ nhật", True), ("tuần sau", True),
    ("chiều", False), ("buổi sáng", False), ("10 giờ", False), ("", False), (None, False),
])
def test_has_explicit_date(text, expected):
    assert de.has_explicit_date(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("sáng thứ 7", "morning"), ("chiều mai", "afternoon"), ("tối nay", "afternoon"),
    ("sáng hoặc chiều", None), ("thứ 7", None), (None, None),
])
def test_period_of(text, expected):
    assert de.period_of(text) == expected


def test_allowed_end_is_end_of_next_month():
    assert de.allowed_end(date(2026, 11, 19)) == date(2026, 12, 31)
    assert de.allowed_end(date(2026, 12, 5)) == date(2027, 1, 31)   # qua năm
    assert de.allowed_end(date(2027, 1, 31)) == date(2027, 2, 28)


# ---- extract_date(): trạng thái cuối cùng. LLM bị thay bằng DateInfo dựng tay ----
@pytest.fixture()
def reads(monkeypatch):
    """reads(DateInfo) -> extract_date sẽ nhận đúng DateInfo này; reads.calls đếm số lần hỏi LLM."""
    class Reader:
        info, calls = None, 0
    reader = Reader()

    def fake_structured(system, user, schema, history=None):
        reader.calls += 1
        return reader.info
    monkeypatch.setattr(de, "generate_structured", fake_structured)

    def set_info(info):
        reader.info = info
        return reader
    return set_info


def test_extract_resolved_date_and_time(reads):
    reads(DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_7, week_offset=0, hour=9, period=Period.AM))
    assert de.extract_date("sáng thứ 7 lúc 9 giờ", TODAY) == {
        "status": de.RESOLVED, "date": "2026-11-21", "time_status": de.RESOLVED, "time": "09:00"}


def test_extract_past_date_is_out_of_range(reads):
    reads(DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_2, week_offset=0))
    assert de.extract_date("thứ 2 tuần này", TODAY)["status"] == de.OUT_OF_RANGE


def test_extract_last_bookable_day_and_the_day_after(reads):
    reads(DateInfo(kind=Kind.MONTH_DAY, day=31, month_offset=1))
    assert de.extract_date("ngày 31 tháng sau", TODAY)["date"] == "2026-12-31"
    reads(DateInfo(kind=Kind.MONTH_DAY, day=1, month=1))
    assert de.extract_date("ngày 1/1", TODAY)["status"] == de.OUT_OF_RANGE  # 01/01/2027 > hết tháng sau


def test_extract_bare_day_part_never_asks_the_llm(reads):
    reader = reads(DateInfo(kind=Kind.RELATIVE, offset=1))
    for text in ("chiều", "buổi sáng", "tối"):
        got = de.extract_date(text, TODAY)
        assert got["status"] == de.NEEDS_CLARIFICATION and got["date"] is None
    assert reader.calls == 0  # không bao giờ đoán ngày từ "chiều"


def test_extract_day_part_is_ignored_when_there_is_no_hour(reads):
    reader = reads(DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.THU_7, week_offset=0))
    assert de.extract_date("chiều thứ 7", TODAY)["date"] == "2026-11-21"
    assert reader.calls == 1


def test_extract_today_is_refused_when_the_words_never_said_today(reads):
    reads(DateInfo(kind=Kind.RELATIVE, offset=0))
    assert de.extract_date("lúc 8 giờ sáng", TODAY)["status"] == de.NEEDS_CLARIFICATION  # LLM tự bịa "hôm nay"
    assert de.extract_date("hôm nay lúc 8 giờ sáng", TODAY)["status"] == de.RESOLVED


def test_extract_without_llm_is_needs_clarification(reads):
    reads(None)
    assert de.extract_date("ngày mai", TODAY) == {
        "status": de.NEEDS_CLARIFICATION, "date": None, "time_status": de.MISSING, "time": None}


def test_extract_hour_without_period_asks_for_period(reads):
    reads(DateInfo(kind=Kind.RELATIVE, offset=1, hour=8))
    got = de.extract_date("ngày mai 8 giờ", TODAY)
    assert (got["status"], got["time_status"], got["time"]) == (de.RESOLVED, de.NEEDS_PERIOD, None)


# ---- sync_date_fact(): chỉ tính lại khi lời khách đổi, giữ ngày tham chiếu cũ ----
def test_sync_date_fact_recomputes_only_when_words_change(reads):
    from app.contracts.schemas import FactSource, KnownFact
    reader = reads(DateInfo(kind=Kind.RELATIVE, offset=1))
    facts = {"appointment_when": KnownFact(key="appointment_when", value="ngày mai", source=FactSource.USER_EXPLICIT)}
    first = de.sync_date_fact(facts)
    assert first["status"] == de.RESOLVED and first["text"] == "ngày mai"
    de.sync_date_fact(facts)
    assert reader.calls == 1  # lần hai dùng lại
    facts["appointment_when"] = KnownFact(key="appointment_when", value="ngày mốt", source=FactSource.USER_CORRECTED)
    de.sync_date_fact(facts)
    assert reader.calls == 2


def test_sync_date_fact_clears_when_there_is_no_time():
    from app.contracts.schemas import FactSource, KnownFact
    facts = {"appointment_date": KnownFact(key="appointment_date", value={"text": "x"},
                                           source=FactSource.APPLICATION_VERIFIED)}
    assert de.sync_date_fact(facts) is None and "appointment_date" not in facts
