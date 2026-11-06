
import os
from enum import Enum
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict


# ---------------- CONFIG ----------------

load_dotenv()

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
client = OpenAI()


# ---------------- LLM OUTPUT ----------------

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


class DateInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    offset: Optional[int] = None       # Relative days
    weekday: Optional[Weekday] = None
    week_offset: Optional[int] = None  # 0=this week, 1=next, 2=week after next
    day: Optional[int] = None
    month: Optional[int] = None
    year: Optional[int] = None
    month_offset: Optional[int] = None # 0=this month, 1=next month


PROMPT = """
Extract the date meaning from the Vietnamese message.
Return components only. NEVER calculate the final date.

Reference date and weekday are provided in the user message.
Calendar weeks run Monday through Sunday.

Rules:

1. RELATIVE:
- hôm nay = offset 0
- ngày mai = 1
- ngày mốt/ngày kia = 2
- hôm qua = -1

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
Vietnamese numeric dates use DD/MM/YYYY.

4. UNKNOWN:
Use UNKNOWN if no exact date can be determined.
Examples:
- tuần sau
- tháng sau
- ngày 15 (month is missing)
- cuối tuần tới (ambiguous)
- đầu tuần sau (not supported by this extractor)

Ignore activity words such as "tôi đến", "tôi qua", "tôi ghé",
"tôi muốn qua", and "tôi qua ký". They do not change the date.

Do not guess missing information. Do not return a final calendar date.
"""


# ---------------- DATE RESOLUTION ----------------

WEEKDAYS = {
    Weekday.THU_2: 0,
    Weekday.THU_3: 1,
    Weekday.THU_4: 2,
    Weekday.THU_5: 3,
    Weekday.THU_6: 4,
    Weekday.THU_7: 5,
    Weekday.CHU_NHAT: 6,
}


def add_months(year: int, month: int, offset: int):
    total = year * 12 + month - 1 + offset
    return total // 12, total % 12 + 1


def resolve(info: DateInfo, today: date):
    if info.kind == Kind.RELATIVE:
        if info.offset is None or not 0 <= info.offset <= 62:
            return None
        return today + timedelta(days=info.offset)

    if info.kind == Kind.WEEKDAY:
        if info.weekday is None:
            return None

        weekday = WEEKDAYS[info.weekday]

        if info.week_offset is None:
            days = (weekday - today.weekday()) % 7
            return today + timedelta(days=days)

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
            year, month = add_months(
                today.year, today.month, info.month_offset
            )
        elif info.month is not None:
            if not 1 <= info.month <= 12:
                return None
            month = info.month
            year = info.year or (
                today.year if month >= today.month else today.year + 1
            )
        else:
            return None

        try:
            return date(year, month, info.day)
        except ValueError:
            return None

    return None


# ---------------- EXTRACTION ----------------

def extract_date(text: str, reference: Optional[datetime] = None):
    now = reference or datetime.now(TZ)

    if now.tzinfo is None:
        raise ValueError("Reference datetime must include a timezone.")

    now = now.astimezone(TZ)
    today = now.date()

    # Allowed range: today through the end of next month.
    next_year, next_month = add_months(today.year, today.month, 1)
    from calendar import monthrange
    end_day = monthrange(next_year, next_month)[1]
    allowed_end = date(next_year, next_month, end_day)

    response = client.responses.parse(
        model=MODEL,
        input=[
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": (
                    f"Today: {today.isoformat()} ({today.strftime('%A')})\n"
                    f"Timezone: Asia/Ho_Chi_Minh\n"
                    f"Expression: {text}"
                ),
            },
        ],
        text_format=DateInfo,
    )

    info = response.output_parsed
    if info is None:
        return {"status": "NEEDS_CLARIFICATION", "date": None}

    result = resolve(info, today)

    if result is None:
        return {
            "status": "NEEDS_CLARIFICATION",
            "date": None,
            "interpretation": info.model_dump(mode="json"),
        }

    if not today <= result <= allowed_end:
        return {
            "status": "OUT_OF_RANGE",
            "date": None,
            "interpretation": info.model_dump(mode="json"),
        }

    return {
        "status": "RESOLVED",
        "date": result.isoformat(),
        "interpretation": info.model_dump(mode="json"),
    }


# ---------------- QUICK TEST ----------------

if __name__ == "__main__":
    reference = datetime.fromisoformat("2026-11-06T09:00:00+07:00")

    tests = [
        # "Ngày mai tôi đến.",
        # "Thứ 7 tuần này tôi qua.",
        # "Chủ nhật tuần này tôi qua.",
        # "Thứ 2 tuần sau tôi đến.",
        # "Thứ 6 tuần tới tôi qua ký.",
        # "Thứ 7 tuần sau nữa.",
        # "Ngày 15 tháng sau tôi đến.",
        # "Ngày 15 tháng 12.",
        # "Tuần sau tôi đến.",
        # "Cuối tuần tới tôi muốn qua.",
        "4 ngày nữa tôi qua"
    ]

    for text in tests:
        print(f"\n{text}")
        print(extract_date(text, reference))
