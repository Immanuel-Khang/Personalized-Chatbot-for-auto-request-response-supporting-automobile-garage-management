"""
[TRACK C] BookingService.
Bản khung: ghi 1 dòng appointments trạng thái PENDING_APPROVAL, chưa gán slot (slot_id NULL);
thời gian khách nói ghi vào note để nhân viên chọn slot khi duyệt.
TODO[BOOKING]: quy đổi thời gian -> appointment_slots, giữ chỗ trong transaction (booked_count), hủy/đổi lịch.
"""
from app.contracts.schemas import BookingResult, ServiceType
from app.db.models import Appointment
from app.db.session import SessionLocal
from app.identity import attach_phone, get_or_create_session, normalize_phone


class DbBookingService:
    def create_appointment(
        self, session_id: str, service_type: ServiceType, when_text: str, phone: str
    ) -> BookingResult:
        contact = normalize_phone(phone)
        if contact is None:
            return BookingResult(ok=False, status="REJECTED", message="Số điện thoại chưa hợp lệ.")
        with SessionLocal() as db:
            session = get_or_create_session(db, session_id)
            customer = attach_phone(db, session, contact)
            row = Appointment(customer_id=customer.id, session_id=session_id, service_type=service_type.value,
                              contact_phone=contact, status="PENDING_APPROVAL",
                              note=f"Thời gian khách đề xuất: {when_text}")
            db.add(row)
            db.commit()
            return BookingResult(ok=True, appointment_id=row.id, status="PENDING_APPROVAL",
                                 message="Lịch hẹn đang chờ nhân viên xác nhận.")
