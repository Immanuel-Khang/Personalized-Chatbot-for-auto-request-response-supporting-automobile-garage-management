"""
[TRACK C] BookingService.
Tạo: ghi 1 dòng appointments PENDING_APPROVAL, chưa gán slot (slot_id NULL), với appointment_date/start_time agent đã
quy đổi; note chỉ chứa xe và yêu cầu riêng của khách. Đổi lịch: ghi ngày giờ mới, trả slot đã giữ và quay về
PENDING_APPROVAL để nhân viên duyệt lại. Hủy: CANCELLED, trả slot đã giữ. Mọi thao tác chỉ chạm lịch của đúng phiên chat (session_id).
TODO[BOOKING]: quy đổi thời gian -> appointment_slots, giữ chỗ trong transaction (booked_count).
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.contracts.schemas import AppointmentInfo, BookingResult, ServiceType
from app.config import settings
from app.db.models import Appointment, AppointmentSlot, Showroom
from app.db.session import SessionLocal
from app.identity import attach_phone, get_or_create_session, normalize_phone

VEHICLE_PREFIX = "Xe: "
DETAIL_PREFIX = "Hạng mục: "
CHANGEABLE = ("PENDING_APPROVAL", "CONFIRMED")
LOCAL_TZ = timezone(timedelta(hours=7))
DEFAULT_SHOWROOM = "Garage One"
SLOT_MINUTES = 60


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

    def reschedule_appointment(self, session_id: str, appointment_id: int, appointment_date: date,
                               start_time: time | None) -> BookingResult:
        with SessionLocal() as db:
            row = _locked(db, session_id, appointment_id)
            if refused := _not_changeable(row):
                return refused
            assert row is not None
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
        starts = datetime.combine(row.appointment_date, when, tzinfo=LOCAL_TZ)
        slot_type = "SHOWROOM_VISIT" if row.service_type == "SHOWROOM_VISIT" else "SERVICE_BAY"
        showroom = _showroom(db)
        db.execute(pg_insert(AppointmentSlot).values(
            showroom_id=showroom.id, slot_type=slot_type, starts_at=starts,
            ends_at=starts + timedelta(minutes=SLOT_MINUTES), capacity=settings.slot_capacity, booked_count=0,
        ).on_conflict_do_nothing(index_elements=["showroom_id", "slot_type", "starts_at"]))
        slot = db.scalars(select(AppointmentSlot).where(
            AppointmentSlot.showroom_id == showroom.id, AppointmentSlot.slot_type == slot_type,
            AppointmentSlot.starts_at == starts).with_for_update()).one()
        if slot.booked_count >= slot.capacity:
            return BookingResult(ok=False, appointment_id=row.id, status="SLOT_FULL",
                                 message=f"Khung giờ này đã đủ chỗ ({slot.booked_count}/{slot.capacity}).")
        slot.booked_count += 1
        row.slot_id, row.start_time, row.status = slot.id, when, "CONFIRMED"
        db.commit()
        return BookingResult(ok=True, appointment_id=row.id, status="CONFIRMED", message="Đã xác nhận lịch hẹn.")
