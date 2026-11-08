"""Appointment date: the LLM reads WHICH date the customer means (components only); code computes the date.

"thứ 7 tuần sau" -> {kind: WEEKDAY, weekday: THU_7, week_offset: 1} -> 2026-11-14. The LLM never counts days,
so a miscount cannot reach a booking. The hour is read the same way (hour + sáng/chiều) and converted to HH:MM here.
``sync_date_fact`` keeps the derived ``appointment_date`` fact in step with
``appointment_when`` (the customer's own words): Preprocessing calls it after each accepted time, and the
appointment agent books only a RESOLVED date, otherwise it asks the customer for a specific day.
"""
import calendar
import logging
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from app.agent.llm import generate_structured
from app.contracts.schemas import FactSource, KnownFact

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
RESOLVED, NEEDS_CLARIFICATION, OUT_OF_RANGE = "RESOLVED", "NEEDS_CLARIFICATION", "OUT_OF_RANGE"
MISSING, NEEDS_PERIOD = "MISSING", "NEEDS_PERIOD"  # time_status values besides RESOLVED


class Kind(str, Enum):
    RELATIVE = "RELATIVE"
    WEEKDAY = "WEEKDAY"
    MONTH_DAY = "MONTH_DAY"
    UNKNOWN = "UNKNOWN"


class Weekday(str, Enum):
    THU_2 = "THU_2"
    THU_3 = "THU_3"
    THU_4 = "THU_4"
    THU_5 = "THU_5"
    THU_6 = "THU_6"
    THU_7 = "THU_7"
    CHU_NHAT = "CHU_NHAT"


class Period(str, Enum):
    AM = "AM"
    PM = "PM"


class DateInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    offset: Optional[int] = None        # relative days
    weekday: Optional[Weekday] = None
    week_offset: Optional[int] = None   # 0=this week, 1=next, 2=week after next
    day: Optional[int] = None
    month: Optional[int] = None
    year: Optional[int] = None
    month_offset: Optional[int] = None  # 0=this month, 1=next month
    hour: Optional[int] = None          # hour as the customer said it: "2 giờ chiều" -> 2, "14h30" -> 14
    minute: Optional[int] = None
    period: Optional[Period] = None     # AM (sáng) | PM (trưa/chiều/tối); null when no day period was said


PROMPT = """
Extract the date and the time of day from the Vietnamese message.
Return components only. NEVER calculate the final date.

Reference date and weekday are provided in the user message.
Calendar weeks run Monday through Sunday.

Rules:

1. RELATIVE:
- hôm nay = offset 0
- ngày mai = 1
- ngày mốt/ngày kia = 2
- hôm qua = -1
- 3 ngày nữa / 3 hôm nữa = 3 (N ngày nữa = N)

2. WEEKDAY:
Use these exact values:
thứ 2=THU_2, thứ 3=THU_3, thứ 4=THU_4,
thứ 5=THU_5, thứ 6=THU_6, thứ 7=THU_7,
chủ nhật=CHU_NHAT.

Week references:
- tuần này=0
- tuần sau/tuần tới=1
- tuần sau nữa=2
If no week is mentioned, week_offset=null.
Never confuse thứ 7 with chủ nhật.

3. MONTH_DAY:
- ngày 15 tháng 12: day=15, month=12
- ngày 15 tháng sau: day=15, month_offset=1
- ngày 15 tháng này: day=15, month_offset=0
- 15/12/2026: day=15, month=12, year=2026
- 15/11 (no year): day=15, month=11, year=null
Vietnamese numeric dates use DD/MM[/YYYY]; the number after the slash is the month.

4. UNKNOWN:
Use UNKNOWN if no exact date can be determined.
Examples:
- tuần sau
- tháng sau
- ngày 15 (month is missing)
- cuối tuần tới (ambiguous)
- đầu tuần sau (not supported by this extractor)
- lúc 8 giờ (time only, no date)

5. TIME OF DAY (independent of the date kind, also with UNKNOWN):
- hour/minute exactly as said: "8 giờ" hour=8; "14h30" hour=14 minute=30; "2 giờ chiều" hour=2.
- period: sáng=AM; trưa/chiều/tối/đêm=PM; null when no such word.
- "sáng"/"chiều" without an hour: hour=null (never invent an hour).
An hour is never a day or a month.

Ignore activity words such as "tôi đến", "tôi qua", "tôi ghé",
"tôi muốn qua", and "tôi qua ký". They do not change the date.

Do not guess missing information. Do not return a final calendar date.
"""

WEEKDAYS = {Weekday.THU_2: 0, Weekday.THU_3: 1, Weekday.THU_4: 2, Weekday.THU_5: 3, Weekday.THU_6: 4,
            Weekday.THU_7: 5, Weekday.CHU_NHAT: 6}


def local_today() -> date:
    return datetime.now(TZ).date()


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    total = year * 12 + month - 1 + offset
    return total // 12, total % 12 + 1


def allowed_end(today: date) -> date:
    """Bookable range: today through the end of next month."""
    year, month = add_months(today.year, today.month, 1)
    return date(year, month, calendar.monthrange(year, month)[1])


def resolve(info: DateInfo, today: date) -> date | None:
    if info.kind == Kind.RELATIVE:
        if info.offset is None or not 0 <= info.offset <= 62:
            return None
        return today + timedelta(days=info.offset)

    if info.kind == Kind.WEEKDAY:
        if info.weekday is None:
            return None
        weekday = WEEKDAYS[info.weekday]
        if info.week_offset is None:
            return today + timedelta(days=(weekday - today.weekday()) % 7)
        if info.week_offset not in (0, 1, 2):
            return None
        monday = today - timedelta(days=today.weekday())
        return monday + timedelta(weeks=info.week_offset, days=weekday)

    if info.kind == Kind.MONTH_DAY:
        if info.day is None or not 1 <= info.day <= 31:
            return None
        if info.month_offset is not None:
            if info.month is not None or info.month_offset not in (0, 1):
                return None
            year, month = add_months(today.year, today.month, info.month_offset)
        elif info.month is not None:
            if not 1 <= info.month <= 12:
                return None
            month = info.month
            year = info.year or (today.year if month >= today.month else today.year + 1)
        else:
            return None
        try:
            return date(year, month, info.day)
        except ValueError:
            return None

    return None


def resolve_time(info: DateInfo) -> tuple[str, str | None]:
    """(time_status, "HH:MM"): MISSING without an hour; NEEDS_PERIOD for 1-11 o'clock said without sáng/chiều
    (could be 08:00 or 20:00); an hour of 13-23 is a 24-hour time and needs no period."""
    hour, minute = info.hour, info.minute or 0
    if hour is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return MISSING, None
    if hour > 12 or hour == 0 or hour == 12:
        pass
    elif info.period is None:
        return NEEDS_PERIOD, None
    elif info.period == Period.PM:
        hour += 12
    return RESOLVED, f"{hour:02d}:{minute:02d}"


def extract_date(text: str, today: date | None = None) -> dict:
    """{"status": RESOLVED | NEEDS_CLARIFICATION | OUT_OF_RANGE, "date": ISO date or None,
    "time_status": RESOLVED | MISSING | NEEDS_PERIOD, "time": "HH:MM" or None}."""
    today = today or local_today()
    info = generate_structured(PROMPT, f"Today: {today.isoformat()} ({today:%A})\nTimezone: Asia/Ho_Chi_Minh\n"
                                       f"Expression: {text}", DateInfo)
    if info is None:
        return {"status": NEEDS_CLARIFICATION, "date": None, "time_status": MISSING, "time": None}
    result = resolve(info, today)
    time_status, hhmm = resolve_time(info)
    logger.info("Date extraction %r -> %s -> %s %s", text, info.model_dump(mode="json", exclude_none=True), result, hhmm)
    clock = {"time_status": time_status, "time": hhmm}
    if result is None:
        return {"status": NEEDS_CLARIFICATION, "date": None, **clock}
    if not today <= result <= allowed_end(today):
        return {"status": OUT_OF_RANGE, "date": None, **clock}
    return {"status": RESOLVED, "date": result.isoformat(), **clock}


def sync_date_fact(facts: dict) -> dict | None:
    """Keep facts["appointment_date"] in step with appointment_when (extracting only when the words changed);
    returns its value ({text, reference, status, date}), or None when there is no appointment time."""
    when = getattr(facts.get("appointment_when"), "value", None)
    if not when:
        facts.pop("appointment_date", None)
        return None
    fact = facts.get("appointment_date")
    if fact is None or not isinstance(fact.value, dict) or fact.value.get("text") != str(when):
        today = local_today()
        # The reference day is kept: "ngày mai" said yesterday still means the date it meant then.
        fact = KnownFact(key="appointment_date", source=FactSource.APPLICATION_VERIFIED,
                         value={"text": str(when), "reference": today.isoformat(), **extract_date(str(when), today)})
        facts["appointment_date"] = fact
    return fact.value
