"""Bộ phân tích hội thoại lúc nhân viên xử lý: khi nhân viên bấm "Xem và duyệt", đọc lại CẢ đợt hỗ trợ (từ lúc nhân
viên vào chat) và TỔNG HỢP thành các đề xuất cập nhật, mỗi ý định đúng một thẻ. Không tự ghi gì: mọi đề xuất nằm ở
bảng staff_actions (PROPOSED) chờ nhân viên duyệt trong hộp thoại (app/api/staff_actions.py thực thi).

Sáu loại hành động (4 nhóm ý định):
  lịch hẹn : BOOK_APPOINTMENT / RESCHEDULE_APPOINTMENT / CANCEL_APPOINTMENT   (một thẻ cho mỗi lịch, lấy trạng thái cuối)
  giá chốt : AGREED_PRICE        (giá cuối cùng đã thỏa thuận)
  khách    : CUSTOMER_INFO       (tên, SĐT, địa chỉ khách; gộp các lần khách/nhân viên nhắc thành một thẻ)
  ghi chú  : HANDLING_NOTE       (MỘT ghi chú tổng hợp cho mỗi chủ đề: thương lượng, khiếu nại, khác)

Mỗi lần quét tính lại từ đầu đợt hỗ trợ và thay thế các đề xuất chưa duyệt (không cộng dồn theo từng tin). Điều đã
duyệt/đã bỏ trong đợt này không bị đề xuất lại: thẻ có tham số giống hệt thì bị loại; ghi chú đã duyệt của một chủ đề
chỉ được viết thêm khi có tin mới hơn mốc tin (source_message_id) mà ghi chú đó đã bao phủ.

Cách phân tích: LLM trả bản nháp có cấu trúc (không tự tính ngày: chỉ trả thành phần như date_extraction.py, code quy
đổi); không có LLM thì dùng quy tắc regex. Cả hai qua cùng bước gộp + kiểm tra: mã lịch phải thuộc phiên và còn đổi
được, ngày không ở quá khứ, giờ HH:MM, SĐT hợp lệ, giá là số nguyên dương. Điều chưa chắc ghi vào warnings để nhân viên
sửa trong hộp thoại, bộ phân tích không đoán.
"""
import logging
import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent.date_extraction import DateInfo, Kind, Weekday, local_today, resolve
from app.agent.llm import generate_structured
from app.db.models import (SERVICE_TYPES, ChatSession, Customer, HandoverTicket, Message, StaffAction)
from app.db.session import SessionLocal
from app.identity import normalize_phone
from app.services.booking import CHANGEABLE, DbBookingService

logger = logging.getLogger(__name__)

SERVICE_LABEL = {"MAINTENANCE": "Bảo dưỡng", "REPAIR": "Sửa chữa", "SHOWROOM_VISIT": "Xem xe tại showroom",
                 "CONTRACT_SIGN": "Ký hợp đồng"}
NOTE_TOPICS = {"SALES_NEGOTIATION": "Thương lượng", "COMPLAINT": "Khiếu nại", "GENERAL": "Khác"}
ActionType = Literal["BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT", "AGREED_PRICE",
                     "CUSTOMER_INFO", "HANDLING_NOTE"]
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ---- bản nháp LLM -----------------------------------------------------------------------------------------------
class ActionDraft(BaseModel):
    type: ActionType
    evidence: str = Field(description="Trích nguyên văn câu làm căn cứ")
    source_message_id: int | None = Field(default=None, description="Số #id của tin nhân viên làm căn cứ chính")
    appointment_id: int | None = Field(default=None, description="Mã lịch trong danh sách LỊCH HẸN HIỆN CÓ (đổi/hủy)")
    service_type: str | None = Field(default=None, description="MAINTENANCE | REPAIR | SHOWROOM_VISIT | CONTRACT_SIGN")
    service_detail: str | None = Field(default=None, description="Hạng mục (thay dầu, phanh...)")
    date: DateInfo | None = Field(default=None, description="Ngày hẹn dạng thành phần; KHÔNG tự tính ngày")
    time: str | None = Field(default=None, description="Giờ 24h HH:MM, chỉ khi nhân viên nêu giờ")
    price_vnd: int | None = Field(default=None, description="Giá chốt cuối cùng, số nguyên VND")
    car_model: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_address: str | None = None
    note: str | None = Field(default=None, description="Nội dung ghi chú tổng hợp (1-3 câu, có số liệu)")
    note_topic: Literal["SALES_NEGOTIATION", "COMPLAINT", "GENERAL"] | None = Field(
        default=None, description="Chủ đề của ghi chú: mỗi chủ đề chỉ MỘT ghi chú")
    reason: str | None = Field(default=None, description="Lý do đổi/hủy lịch nếu nhân viên có nói")


class StaffPlan(BaseModel):
    actions: list[ActionDraft] = Field(default_factory=list)


PROMPT = """\
Bạn là bộ phân tích hội thoại của gara ô tô, lúc NHÂN VIÊN đang trực tiếp trò chuyện với khách. Đọc TOÀN BỘ đoạn
hội thoại của đợt hỗ trợ này và TỔNG HỢP thành các đề xuất cập nhật dữ liệu để trợ lý tự động nắm được khi nhận lại
cuộc trò chuyện. Chỉ đề xuất, không thực hiện.

NGUYÊN TẮC TỔNG HỢP: mỗi ý định đúng MỘT hành động mô tả trạng thái CUỐI CÙNG của cả đoạn hội thoại, KHÔNG tạo một
hành động cho từng tin nhắn. Nếu khách/nhân viên đổi ý giữa chừng (đổi giờ, đổi giá) thì chỉ lấy giá trị cuối.
Việc đặt/đổi/hủy lịch, chốt giá, cam kết phải xuất phát từ NHÂN VIÊN (nói, hứa, hoặc đồng ý rõ ràng với yêu cầu của
khách như "dạ vâng", "ok anh"). Nhân viên chỉ hỏi lại, chào hỏi, nói chung chung thì KHÔNG có hành động. Thông tin
KHÁCH cung cấp (họ tên, SĐT, địa chỉ, giờ/ngày khách hẹn) PHẢI được dùng để điền đầy đủ các trường, kể cả khi khách
nhắn sau tin nhân viên. Không bịa thông tin chưa ai nói; trường nào không rõ thì bỏ trống (null).
Mỗi dòng hội thoại có dạng [#id Vai trò]; điền source_message_id bằng #id của tin làm căn cứ chính.

Loại hành động:
- BOOK_APPOINTMENT: đặt lịch mới (service_type, date, time, service_detail, customer_phone). Một lịch = một hành động.
- RESCHEDULE_APPOINTMENT: đổi lịch ĐÃ CÓ sang ngày/giờ khác (appointment_id từ LỊCH HẸN HIỆN CÓ, date/time mới).
- CANCEL_APPOINTMENT: hủy lịch đã có (appointment_id, reason nếu có).
- AGREED_PRICE: hai bên ĐÃ CHỐT một mức giá cuối cùng (price_vnd = số tiền cuối, VD "700 triệu" -> 700000000;
  car_model nếu rõ). Chưa chốt giá cuối (mới giảm một phần, còn mặc cả) thì KHÔNG dùng, chỉ ghi trong ghi chú thương lượng.
- CUSTOMER_INFO: tên, SĐT, địa chỉ của khách, GỘP thành một hành động. Viết hoa chữ cái đầu của họ tên.
- HANDLING_NOTE: ghi chú tổng hợp để trợ lý nắm bối cảnh. Mỗi chủ đề chỉ MỘT ghi chú tóm tắt cả diễn biến (1-3 câu,
  nêu rõ khách đề nghị gì, nhân viên trả lời gì, kết quả/giá/% cụ thể), đặt note_topic:
    SALES_NEGOTIATION = thương lượng giá, ưu đãi, giảm giá, mua xe (viết tóm tắt cả quá trình mặc cả);
    COMPLAINT = khiếu nại, sự cố và cách đã xử lý;  GENERAL = cam kết/việc cần làm khác.
  Không tạo ghi chú nếu chỉ lặp lại đúng nội dung một hành động khác (VD chỉ có đặt lịch thì không cần ghi chú).

Mục "ĐÃ XỬ LÝ" liệt kê những gì nhân viên đã duyệt/bỏ trong đợt này: KHÔNG đề xuất lại các mục đó. Với ghi chú đã duyệt
tới tin #N của một chủ đề, chỉ tổng hợp phần diễn biến SAU tin #N của chủ đề đó; không có gì mới thì không tạo.
Ngày hẹn: trả date dạng thành phần (RELATIVE/WEEKDAY/MONTH_DAY) theo ngày tham chiếu, không tự tính ngày cuối
("mai" = RELATIVE offset 1). Giờ: 24 giờ, "8 giờ tối" = 20:00, "2h chiều" = 14:00; chưa ai nêu giờ thì time = null.
Khi chỉ có đúng một lịch hẹn hiện có mà nói "đổi/hủy lịch" thì dùng mã lịch đó; nhiều lịch mà không rõ lịch nào thì
để appointment_id = null.
"""


# ---- quy tắc dự phòng (không có LLM) -----------------------------------------------------------------------------
_PHONE = re.compile(r"(?<!\d)((?:\+84|0)\d{9})(?!\d)")
_HOUR = re.compile(r"(?<![\d/])(\d{1,2})\s*(?:h|giờ|g|:)\s*(\d{2})?\s*(?:phút)?\s*(sáng|trưa|chiều|tối)?", re.IGNORECASE)
_DMY = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{4}))?(?!\d)")
_DAY_MONTH = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})", re.IGNORECASE)
_WEEKDAY = re.compile(r"(thứ\s*([2-7])|chủ\s*nhật)(?:\s+(tuần\s+(?:sau|tới|này)))?", re.IGNORECASE)
_PRICE = re.compile(r"(?:chốt|giá\s+cuối|chốt\s+giá|thống\s+nhất)\D{0,25}?(\d[\d.,]*)\s*(tỷ|tỉ|triệu|tr|đồng|vnđ|vnd|đ)\b",
                    re.IGNORECASE)
_NAME = re.compile(r"(?:họ\s*tên|tên\s+khách|khách\s+tên|tên)\s*(?:là|:)?\s+([^\d,.;\n]{2,60}?)(?=\s+(?:sđt|sdt|số|địa\s*chỉ)\b|[\d,.;\n]|$)",
                   re.IGNORECASE)
_MAI = re.compile(r"(?<!\w)mai(?!\w)")  # "mai" viết thường = ngày mai ("Mai" viết hoa có thể là tên người)
_ADDRESS = re.compile(r"địa\s*chỉ\s*(?:là|:)\s*([^\n]{5,160})", re.IGNORECASE)
_NOTE = re.compile(r"^\s*(?:ghi\s*chú|lưu\s*ý)\s*[:\-]?\s*(.+)$", re.IGNORECASE | re.DOTALL)
_HAGGLE = re.compile(r"giảm|bớt|ưu\s*đãi|khuyến\s*mãi|thương\s*lượng|mặc\s*cả|chiết\s*khấu", re.IGNORECASE)
_CANCEL = re.compile(r"\bhủy\s+(?:lịch|hẹn)|\bhuỷ\s+(?:lịch|hẹn)", re.IGNORECASE)
_RESCHEDULE = re.compile(r"(?:đổi|dời|chuyển|sang)\s+(?:lịch|hẹn|sang|qua)|(?:đổi|dời)\s+(?:sang|qua)", re.IGNORECASE)
_BOOK = re.compile(r"(?:đặt|hẹn|book|xếp)\s*(?:cho\s+(?:bạn|anh|chị|em)\s+)?(?:lịch|lúc|vào|ngày)|lịch\s+(?:bảo\s+dưỡng|sửa|hẹn)",
                   re.IGNORECASE)
_SERVICE = ((re.compile(r"ký\s+hợp\s+đồng", re.IGNORECASE), "CONTRACT_SIGN"),
            (re.compile(r"xem\s+xe|showroom", re.IGNORECASE), "SHOWROOM_VISIT"),
            (re.compile(r"bảo\s+dưỡng|thay\s+(?:dầu|nhớt)", re.IGNORECASE), "MAINTENANCE"),
            (re.compile(r"sửa|kiểm\s+tra\s+lỗi", re.IGNORECASE), "REPAIR"))
_WD = {"2": Weekday.THU_2, "3": Weekday.THU_3, "4": Weekday.THU_4, "5": Weekday.THU_5, "6": Weekday.THU_6,
       "7": Weekday.THU_7}
_UNITS = {"tỷ": 1_000_000_000, "tỉ": 1_000_000_000, "triệu": 1_000_000, "tr": 1_000_000}


def _rule_date(text: str, today: date) -> DateInfo | None:
    if m := _DMY.search(text):
        return DateInfo(kind=Kind.MONTH_DAY, day=int(m[1]), month=int(m[2]), year=int(m[3]) if m[3] else None)
    if m := _DAY_MONTH.search(text):
        return DateInfo(kind=Kind.MONTH_DAY, day=int(m[1]), month=int(m[2]))
    lowered = text.lower()
    for words, offset in (("ngày mai", 1), ("ngày mốt", 2), ("ngày kia", 2), ("hôm nay", 0)):
        if words in lowered:
            return DateInfo(kind=Kind.RELATIVE, offset=offset)
    if _MAI.search(text):
        return DateInfo(kind=Kind.RELATIVE, offset=1)
    if m := _WEEKDAY.search(text):
        weekday = Weekday.CHU_NHAT if m[1].lower().startswith("chủ") else _WD[m[2]]
        week = {"sau": 1, "tới": 1, "này": 0}.get((m[3] or "").split()[-1].lower()) if m[3] else None
        return DateInfo(kind=Kind.WEEKDAY, weekday=weekday, week_offset=week)
    return None


def _rule_time(text: str) -> str | None:
    text = _DMY.sub(" ", text)
    m = _HOUR.search(text)
    if not m:
        return None
    hour, minute, part = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
    if part in ("chiều", "tối") and hour < 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def _rule_price(text: str) -> int | None:
    if not (m := _PRICE.search(text)):
        return None
    number, unit = m[1], m[2].lower()
    if unit in _UNITS:  # "780 triệu", "1,2 tỷ" (dấu phẩy/chấm là thập phân)
        try:
            return int(round(float(number.replace(",", ".")) * _UNITS[unit]))
        except ValueError:
            return None
    digits = re.sub(r"\D", "", number)  # "780.000.000 đồng"
    return int(digits) if digits else None


def _rule_drafts(messages: list[tuple[int, str, str]], appointments: list[dict]) -> list[tuple[int, ActionDraft]]:
    """messages: (id, role, text) của đoạn hội thoại, role = staff | user. Ý định (đặt/đổi/hủy, giá, ghi chú) chỉ lấy
    từ tin nhân viên; tên/SĐT/địa chỉ lấy từ cả hai bên; ngày/giờ/SĐT khách nhắn (kể cả sau tin nhân viên) điền vào
    lịch còn thiếu."""
    today, out = local_today(), []
    slots = {"date": None, "time": None, "phone": None}  # giá trị mới nhất trong đoạn hội thoại
    for message_id, role, text in messages:
        if m := _PHONE.search(text):
            slots["phone"] = m[1]
        if when := _rule_date(text, today):
            slots["date"] = when
        if hhmm := _rule_time(_PHONE.sub(" ", text)):
            slots["time"] = hhmm
        name, phone, address = _NAME.search(text), _PHONE.search(text), _ADDRESS.search(text)
        if name or phone or address:
            out.append((message_id, ActionDraft(
                type="CUSTOMER_INFO", evidence=text, customer_name=name[1].strip() if name else None,
                customer_phone=phone[1] if phone else None, customer_address=address[1].strip() if address else None)))
        if role != "staff":
            continue
        sole = appointments[0]["id"] if len(appointments) == 1 else None
        when, hhmm = _rule_date(text, today), _rule_time(_PHONE.sub(" ", text))
        service = next((kind for pattern, kind in _SERVICE if pattern.search(text)), None)
        if _CANCEL.search(text):
            out.append((message_id, ActionDraft(type="CANCEL_APPOINTMENT", evidence=text, appointment_id=sole)))
        elif _RESCHEDULE.search(text):
            out.append((message_id, ActionDraft(type="RESCHEDULE_APPOINTMENT", evidence=text, appointment_id=sole,
                                                date=when, time=hhmm)))
        elif _BOOK.search(text):
            out.append((message_id, ActionDraft(type="BOOK_APPOINTMENT", evidence=text, service_type=service,
                                                date=when, time=hhmm)))
        if price := _rule_price(text):
            out.append((message_id, ActionDraft(type="AGREED_PRICE", evidence=text, price_vnd=price)))
        if note := _NOTE.match(text):
            out.append((message_id, ActionDraft(type="HANDLING_NOTE", evidence=text, note=note[1].strip(),
                                                note_topic="GENERAL")))
    haggle = [(i, t) for i, _, t in messages if _HAGGLE.search(t)]  # không có LLM: gom câu thương lượng thành MỘT ghi chú
    if haggle:
        out.append((haggle[-1][0], ActionDraft(type="HANDLING_NOTE", evidence=haggle[-1][1], note_topic="SALES_NEGOTIATION",
                                               note="Trao đổi giá: " + " | ".join(t.strip() for _, t in haggle)[:450])))
    for _, draft in out:  # điền chỗ trống của lịch bằng điều khách/nhân viên đã nói trong đoạn hội thoại
        if draft.type in ("BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT"):
            draft.date = draft.date or slots["date"]
            draft.time = draft.time or slots["time"]
            if draft.type == "BOOK_APPOINTMENT":
                draft.customer_phone = draft.customer_phone or slots["phone"]
    booked_phones = {d.customer_phone for _, d in out if d.type == "BOOK_APPOINTMENT" and d.customer_phone}
    out = [(i, d) for i, d in out if not (d.type == "CUSTOMER_INFO" and not d.customer_name and not d.customer_address
                                          and d.customer_phone in booked_phones)]  # SĐT đã nằm trong lịch đặt
    # lịch đổi/đặt vẫn thiếu ngày thì đề xuất vẫn hiện để nhân viên chọn; chỉ bỏ khi chẳng có gì để đề xuất
    return [(i, d) for i, d in out if d.type != "RESCHEDULE_APPOINTMENT" or d.date or d.time]


# ---- kiểm tra + chuẩn hóa -----------------------------------------------------------------------------------------
def fmt_vnd(value: int) -> str:
    return f"{value:,}".replace(",", ".") + " đồng"


def describe(action_type: str, params: dict) -> str:
    """Câu mô tả ngắn cho hộp thoại và nhật ký, dựng lại từ params (nên luôn khớp với giá trị nhân viên đã sửa)."""
    when = " ".join(x for x in (
        (f"{date.fromisoformat(params['date']):%d/%m/%Y}" if params.get("date") else "chưa rõ ngày"),
        (f"lúc {params['time']}" if params.get("time") else "")) if x)
    if action_type == "BOOK_APPOINTMENT":
        return f"Đặt lịch {SERVICE_LABEL.get(params.get('service_type'), 'hẹn')} {when}"
    if action_type == "RESCHEDULE_APPOINTMENT":
        return f"Đổi lịch hẹn #{params.get('appointment_id') or '?'} sang {when}"
    if action_type == "CANCEL_APPOINTMENT":
        return f"Hủy lịch hẹn #{params.get('appointment_id') or '?'}"
    if action_type == "AGREED_PRICE":
        car = f" cho {params['car_model']}" if params.get("car_model") else ""
        return f"Ghi nhận giá chốt {fmt_vnd(params['price_vnd']) if params.get('price_vnd') else '?'}{car}"
    if action_type == "CUSTOMER_INFO":
        parts = [f"{label} {params[key]}" for key, label in (("name", "tên"), ("phone", "SĐT"), ("address", "địa chỉ"))
                 if params.get(key)]
        return "Cập nhật thông tin khách: " + (", ".join(parts) or "?")
    return f"Ghi chú {NOTE_TOPICS.get(params.get('topic'), 'xử lý').lower()}: " + (params.get("note") or "?")


def normalize_params(action_type: str, params: dict, appointments: list[dict], customer_phone: str | None = None,
                     today: date | None = None) -> tuple[dict, list[str]]:
    """Params (từ LLM, quy tắc hoặc nhân viên sửa) -> (params sạch, cảnh báo). Không ném lỗi: chỗ nào sai thì bỏ giá trị
    đó đi và nói rõ trong cảnh báo; bước thực thi từ chối thiếu trường bắt buộc."""
    today, warnings, p = today or local_today(), [], {}
    appt_ids = {a["id"] for a in appointments}

    def text(key: str, limit: int = 300) -> str | None:
        value = " ".join(str(params.get(key) or "").split())[:limit]
        return value or None

    def when() -> None:
        raw_date = params.get("date")
        if raw_date:
            try:
                day = date.fromisoformat(str(raw_date))
                if day < today:
                    warnings.append("Ngày đã qua, hãy chọn lại ngày")
                else:
                    p["date"] = day.isoformat()
            except ValueError:
                warnings.append("Ngày không hợp lệ (định dạng YYYY-MM-DD)")
        else:
            warnings.append("Chưa xác định được ngày hẹn, hãy chọn ngày")
        if raw_time := str(params.get("time") or "").strip():
            if m := TIME_RE.match(raw_time):
                p["time"] = f"{int(m[1]):02d}:{m[2]}"
            else:
                warnings.append("Giờ không hợp lệ (định dạng HH:MM)")

    if action_type == "BOOK_APPOINTMENT":
        service = str(params.get("service_type") or "").upper()
        if service in SERVICE_TYPES:
            p["service_type"] = service
        else:
            warnings.append("Chưa rõ loại dịch vụ, hãy chọn lại")
        when()
        phone = normalize_phone(str(params.get("phone") or "")) or customer_phone
        if phone:
            p["phone"] = phone
        else:
            warnings.append("Chưa có số điện thoại khách, hãy nhập")
        if detail := text("detail"):
            p["detail"] = detail
    elif action_type in ("RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT"):
        try:
            appt_id = int(params.get("appointment_id"))
        except (TypeError, ValueError):
            appt_id = None
        if appt_id is None and len(appointments) == 1:
            appt_id = appointments[0]["id"]  # chỉ có một lịch thì không thể là lịch khác
        if appt_id in appt_ids:
            p["appointment_id"] = appt_id
        else:
            warnings.append("Chưa rõ lịch nào" if appt_id is None else f"Lịch #{appt_id} không thuộc cuộc trò chuyện này")
        if action_type == "RESCHEDULE_APPOINTMENT":
            when()
        if reason := text("reason", 200):
            p["reason"] = reason
    elif action_type == "AGREED_PRICE":
        try:
            price = int(params.get("price_vnd"))
        except (TypeError, ValueError):
            price = 0
        if price >= 1_000_000:  # xe/phụ tùng của gara đều từ triệu đồng; số nhỏ hơn nhiều khả năng sai đơn vị
            p["price_vnd"] = price
        else:
            warnings.append("Giá chốt chưa hợp lệ (nhập số tiền VNĐ, tối thiểu 1.000.000)")
        if car := text("car_model", 120):
            p["car_model"] = car
    elif action_type == "CUSTOMER_INFO":
        if name := text("name", 80):
            p["name"] = name.title() if name == name.lower() else name  # "nguyễn văn ba" -> "Nguyễn Văn Ba"
        if raw := str(params.get("phone") or "").strip():
            if phone := normalize_phone(raw):
                p["phone"] = phone
            else:
                warnings.append("Số điện thoại không hợp lệ")
        if address := text("address", 200):
            p["address"] = address
        if not p:
            warnings.append("Chưa có thông tin nào để cập nhật")
    elif action_type == "HANDLING_NOTE":
        if note := text("note", 500):
            p["note"] = note
            p["topic"] = params.get("topic") if params.get("topic") in NOTE_TOPICS else "GENERAL"
        else:
            warnings.append("Ghi chú đang trống")
    else:
        warnings.append("Loại hành động không hỗ trợ")
    return p, warnings


def _draft_params(draft: ActionDraft, today: date) -> dict:
    day = resolve(draft.date, today) if draft.date else None
    base = {"date": day.isoformat() if day else None, "time": draft.time}
    return {
        "BOOK_APPOINTMENT": {**base, "service_type": draft.service_type, "phone": draft.customer_phone,
                             "detail": draft.service_detail},
        "RESCHEDULE_APPOINTMENT": {**base, "appointment_id": draft.appointment_id, "reason": draft.reason},
        "CANCEL_APPOINTMENT": {"appointment_id": draft.appointment_id, "reason": draft.reason},
        "AGREED_PRICE": {"price_vnd": draft.price_vnd, "car_model": draft.car_model},
        "CUSTOMER_INFO": {"name": draft.customer_name, "phone": draft.customer_phone,
                          "address": draft.customer_address},
        "HANDLING_NOTE": {"note": draft.note, "topic": draft.note_topic},
    }[draft.type]


# ---- phân tích một ticket -----------------------------------------------------------------------------------------
def serialize(row: StaffAction) -> dict:
    return {"id": row.id, "ticket_id": row.ticket_id, "type": row.action_type, "status": row.status,
            "params": row.params, "summary": row.summary, "evidence": row.evidence,
            "warnings": row.warnings or [], "result": row.result, "created_at": row.created_at}


def _prompt_user(messages: list[Message], appointments: list[dict], today: date, customer: dict,
                 handled: list[str]) -> str:
    who = {"user": "Khách", "ai": "Bot", "staff": "Nhân viên", "system": "Hệ thống"}
    lines = [f"[#{m.id} {who.get(m.role, m.role)}] {m.content}" for m in messages if m.role != "tool"]
    appts = [f"#{a['id']} {SERVICE_LABEL.get(a['service_type'], a['service_type'])} {a['appointment_date']} "
             f"{a['start_time'] or '(chưa có giờ)'} - {a['status']}" for a in appointments] or ["(không có)"]
    return (f"Ngày tham chiếu: {today.isoformat()} ({today:%A}), múi giờ Asia/Ho_Chi_Minh.\n"
            f"Khách: tên {customer.get('name') or '?'}, SĐT {customer.get('phone') or '?'}\n"
            f"LỊCH HẸN HIỆN CÓ:\n" + "\n".join(appts)
            + "\n\nĐÃ XỬ LÝ trong đợt này (không đề xuất lại):\n" + ("\n".join(handled) or "(chưa có)")
            + "\n\nHỘI THOẠI (vài tin đầu là ngữ cảnh trước khi nhân viên vào chat):\n" + "\n".join(lines))


def _context(db, session: ChatSession) -> tuple[list[dict], dict]:
    customer = db.get(Customer, session.customer_id) if session.customer_id else None
    profile = {"name": customer.full_name if customer else None, "phone": customer.phone_number if customer else None}
    appointments = [a.model_dump() for a in DbBookingService().list_appointments(session.id, active_only=True)]
    return appointments, profile


def _key(action_type: str, params: dict) -> tuple[str, str]:
    return action_type, str(sorted(params.items()))


def list_pending(ticket_id: int) -> list[dict]:
    """Đề xuất đang chờ duyệt của CẢ phiên (nhân viên vào/ra chat nhiều lần = nhiều ticket, mục chưa quyết vẫn còn).
    Mục trùng với mục đã duyệt/đã bỏ của phiên (do bản cũ phân tích lại tin cũ) bị đánh dấu bỏ, không hỏi lại."""
    with SessionLocal() as db:
        session_id = db.get(HandoverTicket, ticket_id).session_id
        rows = db.scalars(select(StaffAction).where(StaffAction.session_id == session_id)
                          .order_by(StaffAction.id)).all()
        decided = {_key(r.action_type, r.params) for r in rows if r.status != "PROPOSED"}
        pending, now = [], datetime.now(timezone.utc)
        for r in rows:
            if r.status != "PROPOSED":
                continue
            if _key(r.action_type, r.params) in decided:
                r.status, r.result, r.decided_at = "DISMISSED", "Trùng với mục đã xử lý", now
            else:
                decided.add(_key(r.action_type, r.params))  # hai mục PROPOSED giống nhau chỉ hiện một
                pending.append(r)
        db.commit()
        return [serialize(r) for r in pending]


def dismiss_pending(ticket_id: int, manager_id: int | None = None) -> int:
    """Bỏ mọi đề xuất chưa duyệt của phiên (nhân viên kết thúc hỗ trợ mà không duyệt). Trả số mục đã bỏ."""
    with SessionLocal() as db:
        session_id = db.get(HandoverTicket, ticket_id).session_id
        rows = db.scalars(select(StaffAction).where(StaffAction.session_id == session_id,
                                                    StaffAction.status == "PROPOSED").with_for_update()).all()
        for r in rows:
            r.status, r.decided_by, r.decided_at = "DISMISSED", manager_id, datetime.now(timezone.utc)
        db.commit()
        return len(rows)


SCHEDULE_TYPES = ("BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT")


def _identity(d: ActionDraft) -> tuple:
    """Hai bản nháp cùng danh tính là cùng một việc -> gộp thành một thẻ (bản sau ghi đè bản trước)."""
    if d.type == "BOOK_APPOINTMENT":
        return d.type, d.service_type or ""
    if d.type in ("RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT"):
        return d.type, d.appointment_id or 0
    if d.type == "HANDLING_NOTE":
        return d.type, d.note_topic or "GENERAL"
    return (d.type,)  # giá chốt, thông tin khách: một thẻ duy nhất


def consolidate(drafts: list[tuple[int, ActionDraft]]) -> list[tuple[int, ActionDraft]]:
    """Mỗi ý định đúng một thẻ: trường nào bản nháp sau có thì ghi đè, trường nào chỉ bản trước có thì giữ lại
    (thông tin khách nhắn rải rác nhiều tin được ghép lại). Các ghi chú cùng chủ đề được nối thành một."""
    merged: dict[tuple, tuple[int, ActionDraft]] = {}
    for message_id, draft in drafts:
        key = _identity(draft)
        if key not in merged:
            merged[key] = (message_id, draft)
            continue
        old_id, old = merged[key]
        new = {k: v for k, v in draft.model_dump(exclude={"type"}).items() if v not in (None, "")}
        if draft.type == "HANDLING_NOTE" and old.note and draft.note and draft.note not in old.note:
            new["note"] = f"{old.note} {draft.note}"[:500]
        merged[key] = (max(old_id, message_id), ActionDraft(**{**old.model_dump(), **new}))
    return list(merged.values())


def _strip_known(params: dict, profile: dict, decided: list[dict]) -> dict:
    """Bỏ phần thông tin khách trùng hồ sơ hiện có hoặc trùng thẻ đã duyệt (đã cập nhật rồi thì không đề xuất lại)."""
    out, known = dict(params), [profile, *decided]
    for key in ("phone", "name", "address"):
        if out.get(key) and any(str(k.get(key) or "").lower() == str(out[key]).lower() for k in known):
            out.pop(key)
    return out


def unscanned_staff(ticket_id: int) -> int:
    """Số tin nhân viên trong đợt hỗ trợ này chưa được tổng hợp (banner nhắc bấm Xem và duyệt, không gọi LLM)."""
    with SessionLocal() as db:
        ticket = db.get(HandoverTicket, ticket_id)
        query = select(func.count()).select_from(Message).where(
            Message.session_id == ticket.session_id, Message.role == "staff", Message.created_at >= ticket.created_at,
            Message.id > (ticket.actions_scanned_message_id or 0))
        if ticket.resolved_at is not None:
            query = query.where(Message.created_at <= ticket.resolved_at)
        return db.scalar(query) or 0


def analyze_ticket(ticket_id: int) -> list[dict]:
    """Tổng hợp cả đợt hỗ trợ của ticket (tin nhân viên + tin khách từ lúc nhân viên vào chat) thành các đề xuất, mỗi ý
    định một thẻ, THAY các đề xuất chưa duyệt trước đó. Đợt chưa có tin mới từ lần tổng hợp trước thì chỉ trả danh sách
    hiện có, không tốn LLM."""
    today = local_today()
    with SessionLocal() as db:
        ticket = db.scalars(select(HandoverTicket).where(HandoverTicket.id == ticket_id).with_for_update()).one()
        sid = ticket.session_id
        window = select(Message).where(Message.session_id == sid, Message.role.in_(["user", "staff"]),
                                       Message.created_at >= ticket.created_at)  # đợt hỗ trợ = từ lúc ticket được tạo
        if ticket.resolved_at is not None:
            window = window.where(Message.created_at <= ticket.resolved_at)
        msgs = db.scalars(window.order_by(Message.id)).all()
        if not any(m.role == "staff" for m in msgs) or msgs[-1].id == ticket.actions_scanned_message_id:
            db.rollback()  # nhả khóa ticket trước khi list_pending mở phiên khác
            return list_pending(ticket_id)
        newest = msgs[-1].id

        session = db.get(ChatSession, sid)
        appointments, profile = _context(db, session)
        episode_rows = db.scalars(select(StaffAction).where(StaffAction.ticket_id == ticket_id)
                                  .order_by(StaffAction.id)).all()
        decided = [r for r in episode_rows if r.status != "PROPOSED"]
        note_floor = {}  # chủ đề -> tin cuối cùng mà ghi chú đã duyệt/bỏ bao phủ
        for r in decided:
            if r.action_type == "HANDLING_NOTE":
                topic = r.params.get("topic", "GENERAL")
                note_floor[topic] = max(note_floor.get(topic, 0), r.source_message_id or 0)

        before = list(reversed(db.scalars(select(Message).where(
            Message.session_id == sid, Message.role.in_(["user", "ai", "staff"]), Message.id < msgs[0].id)
            .order_by(Message.id.desc()).limit(6)).all()))
        ai_between = db.scalars(select(Message).where(Message.session_id == sid, Message.role == "ai",
                                                      Message.id >= msgs[0].id)).all()
        shown = sorted([*before, *msgs, *ai_between], key=lambda m: m.id)
        handled = [f"[{'Đã duyệt' if r.status == 'APPLIED' else 'Đã bỏ'}] {r.summary}"
                   + (f" (tới tin #{r.source_message_id})" if r.action_type == "HANDLING_NOTE" else "") for r in decided]
        plan = generate_structured(PROMPT, _prompt_user(shown, appointments, today, profile, handled), StaffPlan)
        if plan is not None:
            drafts = [(d.source_message_id if d.source_message_id in {m.id for m in msgs} else newest, d)
                      for d in plan.actions]
        else:
            drafts = _rule_drafts([(m.id, m.role, m.content) for m in msgs], appointments)
        drafts = consolidate(drafts)

        decided_keys = {_key(r.action_type, r.params) for r in decided}
        decided_params = [r.params for r in decided if r.action_type == "CUSTOMER_INFO"]
        applied_sched = [r for r in db.scalars(select(StaffAction).where(
            StaffAction.session_id == sid, StaffAction.status == "APPLIED",
            StaffAction.action_type.in_(SCHEDULE_TYPES))).all()]
        db.query(StaffAction).filter(StaffAction.session_id == sid, StaffAction.status == "PROPOSED").delete()
        for _, draft in drafts:
            params, warnings = normalize_params(draft.type, _draft_params(draft, today), appointments, profile["phone"], today)
            if draft.type == "CUSTOMER_INFO":
                params = _strip_known(params, profile, decided_params)
                if not params:
                    continue  # chỉ nhắc lại điều hồ sơ/thẻ đã duyệt đã có
                warnings = []
            if draft.type == "HANDLING_NOTE" and "note" in params and newest <= note_floor.get(params["topic"], 0):
                continue  # không có diễn biến mới hơn ghi chú đã duyệt của chủ đề này
            if _key(draft.type, params) in decided_keys:
                continue  # đã duyệt hoặc đã bỏ: không hỏi lại
            if draft.type == "AGREED_PRICE" and any(r.action_type == draft.type and r.params.get("price_vnd")
                                                    == params.get("price_vnd") for r in decided):
                continue
            if any(r.action_type == draft.type and _same_schedule(draft.type, r.params, params) for r in applied_sched):
                continue  # lịch này đã được duyệt rồi
            db.add(StaffAction(ticket_id=ticket_id, session_id=sid, source_message_id=newest, action_type=draft.type,
                               params=params, summary=describe(draft.type, params),
                               evidence=(draft.evidence or "")[:500] or None, warnings=warnings or None))
        ticket.actions_scanned_message_id = newest
        db.commit()
    return list_pending(ticket_id)


def _same_schedule(action_type: str, old: dict, new: dict) -> bool:
    """Bản nháp lịch có trùng một lịch đã được duyệt không (cùng dịch vụ + ngày, hoặc cùng mã lịch)."""
    def agree(key: str) -> bool:
        return not old.get(key) or not new.get(key) or old[key] == new[key]
    if action_type == "BOOK_APPOINTMENT":
        return bool(new.get("date")) and agree("service_type") and old.get("date") == new.get("date")
    return bool(new.get("appointment_id")) and old.get("appointment_id") == new.get("appointment_id") and (
        action_type == "CANCEL_APPOINTMENT" or (old.get("date"), old.get("time")) == (new.get("date"), new.get("time")))


def context_for(ticket_id: int) -> dict:
    """Dữ liệu hộp thoại cần để nhân viên sửa đề xuất: lịch còn đổi/hủy được và SĐT khách."""
    with SessionLocal() as db:
        ticket = db.get(HandoverTicket, ticket_id)
        appointments, profile = _context(db, db.get(ChatSession, ticket.session_id))
        return {"appointments": [a for a in appointments if a["status"] in CHANGEABLE], "customer": profile,
                "service_types": [{"value": s, "label": SERVICE_LABEL[s]} for s in SERVICE_TYPES]}
