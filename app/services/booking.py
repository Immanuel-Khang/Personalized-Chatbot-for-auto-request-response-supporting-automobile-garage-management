"""
[TRACK C] BookingService.
Tạo: ghi 1 dòng appointments PENDING_APPROVAL, chưa gán slot (slot_id NULL), với appointment_date/start_time agent đã
quy đổi; note chỉ chứa xe và yêu cầu riêng của khách. Đổi lịch: ghi ngày giờ mới, trả slot đã giữ và quay về
PENDING_APPROVAL để nhân viên duyệt lại. Hủy: CANCELLED, trả slot đã giữ. Mọi thao tác chỉ chạm lịch của đúng phiên chat (session_id).
Chống trùng/đầy: khi tạo hoặc đổi lịch có giờ cụ thể, kiểm tra khung giờ (lưới theo BookingConfig) ngay trong transaction, khóa theo ngày
(pg_advisory_xact_lock) nên hai khách đặt cùng lúc không vượt sức chứa. Lịch PENDING_APPROVAL và CONFIRMED đều giữ chỗ.
Nhân viên xác nhận thì kiểm tra lại theo số lịch đã CONFIRMED. Sức chứa và độ dài khung giờ chỉnh được ở trang admin (app_settings).
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.contracts.schemas import AppointmentInfo, BookingResult, ServiceType
from app.config import settings
from app.db.models import AppSetting, Appointment, AppointmentSlot, ChatSession, Customer, Showroom
from app.db.session import SessionLocal
from app.identity import attach_phone, get_or_create_session, normalize_phone
from app.services.slots import (BookingConfig, minutes_of, parse_hhmm, to_time, window_label, window_start,
                                work_windows)

VEHICLE_PREFIX = "Xe: "
DETAIL_PREFIX = "Hạng mục: "
CHANGEABLE = ("PENDING_APPROVAL", "CONFIRMED")
LOCAL_TZ = timezone(timedelta(hours=7))
DEFAULT_SHOWROOM = "Garage One"
HOLDING = ("PENDING_APPROVAL", "CONFIRMED")  # trạng thái đang giữ chỗ trong khung giờ
CFG_PREFIX = "booking."
CFG_FIELDS = ("slot_capacity", "slot_minutes", "work_start", "work_end")


def _note_value(note: str | None, prefix: str) -> str | None:
    return next((line[len(prefix):].strip() for line in (note or "").splitlines() if line.startswith(prefix)), None)


def _info(db: Session, row: Appointment) -> AppointmentInfo:
    slot = db.get(AppointmentSlot, row.slot_id) if row.slot_id else None
    return AppointmentInfo(
        id=row.id, service_type=row.service_type, status=row.status,
        appointment_date=row.appointment_date.isoformat() if row.appointment_date else None,
        start_time=row.start_time.strftime("%H:%M") if row.start_time else None,
        vehicle=_note_value(row.note, VEHICLE_PREFIX),
        detail=_note_value(row.note, DETAIL_PREFIX),
        slot_start=slot.starts_at.astimezone(LOCAL_TZ).strftime("%H:%M %d/%m/%Y") if slot else None)


def _release_slot(db: Session, row: Appointment) -> None:
    if row.slot_id is None:
        return
    slot = db.get(AppointmentSlot, row.slot_id, with_for_update=True)
    if slot and slot.booked_count > 0:
        slot.booked_count -= 1
    row.slot_id = None


def _locked(db: Session, session_id: str, appointment_id: int) -> Appointment | None:
    """The session's appointment, row-locked for the update."""
    return db.scalars(select(Appointment).where(Appointment.id == appointment_id, Appointment.session_id == session_id)
                      .with_for_update()).one_or_none()


def _not_changeable(row: Appointment | None) -> BookingResult | None:
    if row is None:
        return BookingResult(ok=False, status="NOT_FOUND", message="Không tìm thấy lịch hẹn này.")
    if row.status not in CHANGEABLE:
        return BookingResult(ok=False, appointment_id=row.id, status=row.status,
                             message="Lịch hẹn này đã kết thúc hoặc đã hủy, không thay đổi được.")
    return None


# ---------------------------------------------------------------- cấu hình khung giờ
def _default_config() -> BookingConfig:
    def hhmm(raw: str, fallback: time) -> time:
        try:
            return parse_hhmm(raw)
        except ValueError:
            return fallback
    return BookingConfig(slot_capacity=settings.slot_capacity, slot_minutes=settings.slot_minutes,
                         work_start=hhmm(settings.work_start, time(8, 0)), work_end=hhmm(settings.work_end, time(17, 0)))


def _config(db: Session) -> BookingConfig:
    """Giá trị admin đã chỉnh (app_settings) đè lên mặc định từ biến môi trường."""
    base = _default_config()
    rows = dict(db.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.like(CFG_PREFIX + "%"))).all())
    values = {}
    for field in CFG_FIELDS:
        raw = rows.get(CFG_PREFIX + field)
        if raw is None:
            continue
        try:
            values[field] = parse_hhmm(raw) if field.startswith("work_") else int(raw)
        except ValueError:
            continue  # giá trị hỏng trong DB: dùng mặc định
    return BookingConfig(**{**base.__dict__, **values})


def get_booking_config() -> BookingConfig:
    with SessionLocal() as db:
        return _config(db)


def default_booking_config() -> BookingConfig:
    return _default_config()


def set_booking_config(cfg: BookingConfig) -> BookingConfig:
    """Lưu cấu hình (người gọi đã kiểm tra bằng slots.validate). Chỉ ảnh hưởng đặt/xác nhận từ giờ trở đi; lịch cũ giữ nguyên."""
    pairs = {"slot_capacity": str(cfg.slot_capacity), "slot_minutes": str(cfg.slot_minutes),
             "work_start": f"{cfg.work_start:%H:%M}", "work_end": f"{cfg.work_end:%H:%M}"}
    with SessionLocal() as db:
        for field, value in pairs.items():
            stmt = pg_insert(AppSetting).values(key=CFG_PREFIX + field, value=value)
            db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_at": func.now()}))
        db.commit()
        return _config(db)


# ---------------------------------------------------------------- kiểm tra trùng / đầy
def _slot_type(service_type: str) -> str:
    return "SHOWROOM_VISIT" if service_type == "SHOWROOM_VISIT" else "SERVICE_BAY"


def _lock_day(db: Session, day: date) -> None:
    """Tuần tự hóa mọi thao tác đặt/đổi/xác nhận trong cùng một ngày (nhả khi transaction kết thúc)."""
    db.execute(select(func.pg_advisory_xact_lock(7_000_000 + day.toordinal())))


def _rows_on(db: Session, day: date, slot_type: str, statuses: tuple[str, ...], exclude_id: int | None) -> list[Appointment]:
    query = select(Appointment).where(Appointment.appointment_date == day, Appointment.status.in_(statuses),
                                      Appointment.start_time.is_not(None))
    query = query.where(Appointment.service_type == "SHOWROOM_VISIT" if slot_type == "SHOWROOM_VISIT"
                        else Appointment.service_type != "SHOWROOM_VISIT")
    if exclude_id is not None:
        query = query.where(Appointment.id != exclude_id)
    return list(db.scalars(query))


def _in_window(rows: list[Appointment], window: time, cfg: BookingConfig) -> list[Appointment]:
    return [r for r in rows if window_start(r.start_time, cfg) == window]


def _alternatives(rows: list[Appointment], day: date, wanted: time, cfg: BookingConfig, limit: int = 3) -> list[str]:
    """Các khung giờ còn trống cùng ngày (trong giờ làm việc), gần giờ khách muốn nhất, xếp theo thời gian."""
    now = datetime.now(LOCAL_TZ)
    free = [w for w in work_windows(cfg) if w != wanted and len(_in_window(rows, w, cfg)) < cfg.slot_capacity
            and not (day == now.date() and minutes_of(w) <= now.hour * 60 + now.minute)]
    free.sort(key=lambda w: abs(minutes_of(w) - minutes_of(wanted)))
    return [f"{w:%H:%M}" for w in sorted(free[:limit])]


def _conflict(db: Session, cfg: BookingConfig, customer_id: int | None, service_type: str, day: date,
              start: time | None, exclude_id: int | None = None) -> BookingResult | None:
    """None nếu đặt được; ngược lại SLOT_FULL (khung giờ đủ chỗ) hoặc DUPLICATE (khách đã có lịch cùng khung giờ).
    Lịch chưa có giờ cụ thể (chỉ có ngày) chưa chiếm chỗ nên không kiểm tra."""
    if start is None:
        return None
    window, slot_type = window_start(start, cfg), _slot_type(service_type)
    rows = _rows_on(db, day, slot_type, HOLDING, exclude_id)
    inside = _in_window(rows, window, cfg)
    label = f"{window_label(window, cfg)} ngày {day:%d/%m/%Y}"
    mine = next((r for r in inside if customer_id is not None and r.customer_id == customer_id), None)
    if mine is not None:
        return BookingResult(ok=False, appointment_id=mine.id, status="DUPLICATE",
                             message=f"Bạn đã có lịch #{mine.id} lúc {mine.start_time:%H:%M} ngày {day:%d/%m/%Y}, "
                                     f"cùng khung giờ {window_label(window, cfg)}, nên không đặt thêm trùng được.")
    if len(inside) >= cfg.slot_capacity:
        alts = _alternatives(rows, day, window, cfg)
        hint = f" Còn trống: {', '.join(alts)}." if alts else " Ngày này không còn khung giờ trống."
        return BookingResult(ok=False, status="SLOT_FULL", alternatives=alts,
                             message=f"Khung giờ {label} đã đủ chỗ ({len(inside)}/{cfg.slot_capacity}).{hint}")
    return None


def slot_overview(day: date, slot_type: str = "SERVICE_BAY") -> dict:
    """Số lịch đang giữ chỗ từng khung giờ trong ngày (cho trang admin)."""
    with SessionLocal() as db:
        cfg = _config(db)
        pending = _rows_on(db, day, slot_type, ("PENDING_APPROVAL",), None)
        confirmed = _rows_on(db, day, slot_type, ("CONFIRMED",), None)
        windows = []
        for w in work_windows(cfg):
            p, c = _in_window(pending, w, cfg), _in_window(confirmed, w, cfg)
            windows.append({"start": f"{w:%H:%M}", "label": window_label(w, cfg), "confirmed": len(c), "pending": len(p),
                            "capacity": cfg.slot_capacity, "free": max(0, cfg.slot_capacity - len(c) - len(p)),
                            "appointment_ids": sorted(r.id for r in [*c, *p])})
        unscheduled = db.scalar(select(func.count()).select_from(Appointment).where(
            Appointment.appointment_date == day, Appointment.status.in_(HOLDING), Appointment.start_time.is_(None)))
        return {"date": day.isoformat(), "slot_type": slot_type, "slot_capacity": cfg.slot_capacity,
                "slot_minutes": cfg.slot_minutes, "windows": windows, "unscheduled": unscheduled or 0}


class DbBookingService:
    def create_appointment(
        self, session_id: str, service_type: ServiceType, appointment_date: date, start_time: time | None,
        phone: str, detail: str | None = None,
    ) -> BookingResult:
        contact = normalize_phone(phone)
        if contact is None:
            return BookingResult(ok=False, status="REJECTED", message="Số điện thoại chưa hợp lệ.")
        with SessionLocal() as db:
            session = get_or_create_session(db, session_id)
            customer = attach_phone(db, session, contact)
            if customer is None:
                return BookingResult(ok=False, status="REJECTED", message="Số điện thoại chưa hợp lệ.")
            _lock_day(db, appointment_date)  # kiểm tra + ghi trong cùng khóa ngày: không vượt sức chứa khi đặt đồng thời
            if refused := _conflict(db, _config(db), customer.id, service_type.value, appointment_date, start_time):
                return refused
            row = Appointment(customer_id=customer.id, session_id=session_id, service_type=service_type.value,
                              contact_phone=contact, status="PENDING_APPROVAL", appointment_date=appointment_date,
                              start_time=start_time, note=detail)
            db.add(row)
            db.commit()
            return BookingResult(ok=True, appointment_id=row.id, status="PENDING_APPROVAL",
                                 message="Lịch hẹn đang chờ nhân viên xác nhận.")

    def list_appointments(self, session_id: str, active_only: bool = False) -> list[AppointmentInfo]:
        with SessionLocal() as db:
            query = select(Appointment).where(Appointment.session_id == session_id)
            if active_only:
                query = query.where(Appointment.status.in_(CHANGEABLE))
            return [_info(db, row) for row in db.scalars(query.order_by(Appointment.id.desc()))]

    def check_slot(self, session_id: str, service_type: ServiceType | str, appointment_date: date,
                   start_time: time | None, exclude_id: int | None = None, phone: str | None = None) -> BookingResult:
        """Hỏi trước khi đề xuất với khách: còn chỗ không, có bị trùng lịch của chính khách không. Không ghi gì."""
        with SessionLocal() as db:
            customer_id = None
            if exclude_id is not None:
                row = db.get(Appointment, exclude_id)
                customer_id = row.customer_id if row else None
            if customer_id is None and phone and (contact := normalize_phone(phone)):
                customer_id = db.scalar(select(Customer.id).where(Customer.phone_number == contact))
            if customer_id is None and (session := db.get(ChatSession, session_id)) is not None:
                customer_id = session.customer_id
            conflict = _conflict(db, _config(db), customer_id, getattr(service_type, "value", service_type),
                                 appointment_date, start_time, exclude_id)
            return conflict or BookingResult(ok=True, status="AVAILABLE", message="Khung giờ còn trống.")

    def reschedule_appointment(self, session_id: str, appointment_id: int, appointment_date: date,
                               start_time: time | None) -> BookingResult:
        with SessionLocal() as db:
            row = _locked(db, session_id, appointment_id)
            if refused := _not_changeable(row):
                return refused
            assert row is not None
            _lock_day(db, appointment_date)
            if refused := _conflict(db, _config(db), row.customer_id, row.service_type, appointment_date, start_time,
                                    exclude_id=row.id):
                return refused  # lịch cũ giữ nguyên (chưa trả slot)
            row.appointment_date, row.start_time = appointment_date, start_time
            _release_slot(db, row)  # a staff-chosen slot no longer matches the new time
            row.status = "PENDING_APPROVAL"
            db.commit()
            return BookingResult(ok=True, appointment_id=row.id, status=row.status,
                                 message="Lịch hẹn mới đang chờ nhân viên xác nhận.")

    def cancel_appointment(self, session_id: str, appointment_id: int) -> BookingResult:
        with SessionLocal() as db:
            row = _locked(db, session_id, appointment_id)
            if refused := _not_changeable(row):
                return refused
            assert row is not None
            _release_slot(db, row)
            row.status = "CANCELLED"
            db.commit()
            return BookingResult(ok=True, appointment_id=row.id, status=row.status, message="Lịch hẹn đã được hủy.")


def _showroom(db: Session) -> Showroom:
    row = db.scalars(select(Showroom).where(Showroom.is_active).order_by(Showroom.id)).first()
    if row is None:  # chưa khai báo showroom: tạo mặc định để gán được khung giờ
        row = Showroom(name=DEFAULT_SHOWROOM)
        db.add(row)
        db.flush()
    return row


def confirm_appointment(appointment_id: int, start_time: time | None = None) -> BookingResult:
    """Nhân viên xác nhận lịch PENDING_APPROVAL: gán khung giờ (tạo nếu chưa có) và tăng booked_count trong transaction.
    Cố ý nằm ngoài BookingService của agent: chatbot không tự xác nhận được. start_time chỉ dùng khi khách chưa nêu giờ."""
    with SessionLocal() as db:
        row = db.scalars(select(Appointment).where(Appointment.id == appointment_id).with_for_update()).one_or_none()
        if row is None:
            return BookingResult(ok=False, status="NOT_FOUND", message="Không tìm thấy lịch hẹn này.")
        if row.status != "PENDING_APPROVAL":
            return BookingResult(ok=False, appointment_id=row.id, status=row.status,
                                 message="Lịch hẹn này không còn ở trạng thái chờ xác nhận.")
        if row.appointment_date is None:
            return BookingResult(ok=False, appointment_id=row.id, status=row.status,
                                 message="Lịch hẹn chưa có ngày, hãy nhắn khách để chốt ngày trước.")
        when = row.start_time or start_time
        if when is None:
            return BookingResult(ok=False, appointment_id=row.id, status="NEEDS_TIME",
                                 message="Khách chưa chọn giờ, hãy nhập giờ hẹn.")
        cfg = _config(db)
        slot_type = _slot_type(row.service_type)
        _lock_day(db, row.appointment_date)
        window = window_start(when, cfg)
        confirmed = _in_window(_rows_on(db, row.appointment_date, slot_type, ("CONFIRMED",), row.id), window, cfg)
        if len(confirmed) >= cfg.slot_capacity:
            return BookingResult(ok=False, appointment_id=row.id, status="SLOT_FULL",
                                 message=f"Khung giờ này đã đủ chỗ ({len(confirmed)}/{cfg.slot_capacity}).")
        starts = datetime.combine(row.appointment_date, window, tzinfo=LOCAL_TZ)
        showroom = _showroom(db)
        db.execute(pg_insert(AppointmentSlot).values(
            showroom_id=showroom.id, slot_type=slot_type, starts_at=starts,
            ends_at=starts + timedelta(minutes=cfg.slot_minutes), capacity=cfg.slot_capacity, booked_count=0,
        ).on_conflict_do_nothing(index_elements=["showroom_id", "slot_type", "starts_at"]))
        slot = db.scalars(select(AppointmentSlot).where(
            AppointmentSlot.showroom_id == showroom.id, AppointmentSlot.slot_type == slot_type,
            AppointmentSlot.starts_at == starts).with_for_update()).one()
        slot.capacity = max(cfg.slot_capacity, slot.booked_count + 1)  # theo cấu hình hiện hành; không thấp hơn số đã giữ
        slot.booked_count += 1
        row.slot_id, row.start_time, row.status = slot.id, when, "CONFIRMED"
        db.commit()
        return BookingResult(ok=True, appointment_id=row.id, status="CONFIRMED", message="Đã xác nhận lịch hẹn.")


def complete_appointment(appointment_id: int) -> BookingResult:
    """Nhân viên đánh dấu lịch CONFIRMED đã làm xong -> DONE. Lịch giữ slot_id (ràng buộc DB) nhưng trả chỗ trong khung giờ
    (DONE không còn tính là giữ chỗ). Lịch DONE không đổi/hủy được nữa. Bot không có quyền này."""
    with SessionLocal() as db:
        row = db.scalars(select(Appointment).where(Appointment.id == appointment_id).with_for_update()).one_or_none()
        if row is None:
            return BookingResult(ok=False, status="NOT_FOUND", message="Không tìm thấy lịch hẹn này.")
        if row.status != "CONFIRMED":
            return BookingResult(ok=False, appointment_id=row.id, status=row.status,
                                 message="Chỉ lịch đã xác nhận mới chuyển sang hoàn thành được.")
        slot = db.get(AppointmentSlot, row.slot_id, with_for_update=True) if row.slot_id else None
        if slot is not None and slot.booked_count > 0:
            slot.booked_count -= 1
        row.status = "DONE"
        db.commit()
        return BookingResult(ok=True, appointment_id=row.id, status="DONE", message="Đã hoàn thành lịch hẹn.")
