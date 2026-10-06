"""
[TRACK C] BookingService.
Bản khung: ghi thẳng 1 dòng appointments, trạng thái PENDING_APPROVAL.
TODO[BOOKING]: giờ làm việc, slot capacity (transactional hold), tự xác nhận hay chờ duyệt, hủy/đổi lịch.
"""
from app.contracts.schemas import BookingResult, ServiceType
from app.db.models import Appointment
from app.db.session import SessionLocal


class DbBookingService:
    def create_appointment(
        self, customer_id: int, service_type: ServiceType, when_text: str, phone: str
    ) -> BookingResult:
        with SessionLocal() as db:
            row = Appointment(customer_id=customer_id, service_type=service_type.value,
                              when_text=when_text, phone=phone, status="PENDING_APPROVAL")
            db.add(row)
            db.commit()
            return BookingResult(ok=True, appointment_id=row.id, status="PENDING_APPROVAL",
                                 message="Lịch hẹn đang chờ nhân viên xác nhận.")
