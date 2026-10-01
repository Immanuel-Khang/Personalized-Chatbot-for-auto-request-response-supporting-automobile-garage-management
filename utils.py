from datetime import date, timedelta

WEEKDAY_MAP = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def get_next_weekday(today: date, weekday: str) -> date:
    target = WEEKDAY_MAP[weekday]

    days_ahead = (target - today.weekday()) % 7

    # "thứ 6" means the upcoming Friday,
    # not today if today itself is Friday.
    if days_ahead == 0:
        days_ahead = 7

    return today + timedelta(days=days_ahead)