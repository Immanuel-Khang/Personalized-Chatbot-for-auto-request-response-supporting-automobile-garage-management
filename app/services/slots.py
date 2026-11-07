"""Khung giờ đặt lịch: tính toán thuần (không DB) để dùng chung cho booking, admin và test.
Lưới khung giờ bắt đầu từ giờ mở cửa (work_start) và cách nhau slot_minutes phút.
Ví dụ work_start=08:00, slot_minutes=90 -> 08:00-09:30, 09:30-11:00, 11:00-12:30 ...
Một khung giờ nhận tối đa slot_capacity lịch (số xe/khách làm cùng lúc)."""
from dataclasses import dataclass
from datetime import time

MIN_SLOT_MINUTES, MAX_SLOT_MINUTES = 10, 480
MIN_CAPACITY, MAX_CAPACITY = 1, 50


@dataclass(frozen=True)
class BookingConfig:
    slot_capacity: int = 2      # số lịch tối đa trong một khung giờ
    slot_minutes: int = 60      # độ dài một khung giờ = khoảng cách giữa hai khung liên tiếp
    work_start: time = time(8, 0)
    work_end: time = time(17, 0)


def minutes_of(t: time) -> int:
    return t.hour * 60 + t.minute


def to_time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def parse_hhmm(raw: str) -> time:
    """'08:30' -> time(8, 30). ValueError nếu sai định dạng."""
    h, m = str(raw).strip().split(":")
    return time(int(h), int(m))


def window_start(t: time, cfg: BookingConfig) -> time:
    """Giờ bắt đầu của khung giờ chứa t (giờ trước work_start vẫn được gom theo cùng lưới, không âm)."""
    base = minutes_of(cfg.work_start)
    start = base + ((minutes_of(t) - base) // cfg.slot_minutes) * cfg.slot_minutes
    return to_time(max(0, start))


def window_end_minutes(start: time, cfg: BookingConfig) -> int:
    return minutes_of(start) + cfg.slot_minutes


def window_label(start: time, cfg: BookingConfig) -> str:
    end = min(window_end_minutes(start, cfg), 24 * 60 - 1)
    return f"{start:%H:%M}–{to_time(end):%H:%M}"


def work_windows(cfg: BookingConfig) -> list[time]:
    """Các khung giờ nằm trọn trong [work_start, work_end]."""
    out, cur, end = [], minutes_of(cfg.work_start), minutes_of(cfg.work_end)
    while cur + cfg.slot_minutes <= end:
        out.append(to_time(cur))
        cur += cfg.slot_minutes
    return out


def validate(capacity: int, slot_minutes: int, work_start: time, work_end: time) -> str | None:
    """Thông báo lỗi tiếng Việt, None nếu hợp lệ."""
    if not MIN_CAPACITY <= capacity <= MAX_CAPACITY:
        return f"Số lịch mỗi khung giờ phải từ {MIN_CAPACITY} đến {MAX_CAPACITY}."
    if not MIN_SLOT_MINUTES <= slot_minutes <= MAX_SLOT_MINUTES:
        return f"Độ dài khung giờ phải từ {MIN_SLOT_MINUTES} đến {MAX_SLOT_MINUTES} phút."
    if minutes_of(work_start) >= minutes_of(work_end):
        return "Giờ mở cửa phải sớm hơn giờ đóng cửa."
    if minutes_of(work_end) - minutes_of(work_start) < slot_minutes:
        return "Khoảng giờ làm việc ngắn hơn một khung giờ."
    return None
