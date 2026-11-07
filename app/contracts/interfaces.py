"""
HỢP ĐỒNG HÀM giữa Agent (người B) và các Service (người A, C).
Agent chỉ được gọi các hàm dưới đây, KHÔNG được import trực tiếp DB hay thư viện RAG.
Nhờ vậy mỗi người viết phần của mình độc lập, ghép lại không vỡ.
"""
from datetime import date, time
from typing import Optional, Protocol

from app.contracts.schemas import (
    AppointmentInfo, BookingResult, HandoverResult, KnowledgeChunk,
    MaintenanceResult, Product, ServiceRecord, ServiceType,
)


class ProductService(Protocol):
    def search(self, query: str = "", limit: int = 3, fallback: bool = True,
               max_budget_vnd: float | None = None) -> list[Product]: ...
    def find_model(self, name: str, limit: int = 3) -> list[Product]: ...
    def list_vehicles(self, limit: int = 10) -> list[Product]: ...
    def vehicle_summary(self) -> dict[str, int]: ...
    def get_by_id(self, product_id: int) -> Optional[Product]: ...
    def get_by_name(self, name: str) -> Optional[Product]: ...


class KnowledgeService(Protocol):
    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]: ...


class BookingService(Protocol):
    def create_appointment(
        self, session_id: str, service_type: ServiceType, appointment_date: date, start_time: time | None,
        phone: str, detail: str | None = None,
    ) -> BookingResult: ...
    def list_appointments(self, session_id: str, active_only: bool = False) -> list[AppointmentInfo]: ...
    def reschedule_appointment(self, session_id: str, appointment_id: int, appointment_date: date,
                               start_time: time | None) -> BookingResult: ...
    def check_slot(self, session_id: str, service_type: ServiceType, appointment_date: date, start_time: time | None,
                   exclude_id: int | None = None) -> BookingResult: ...  # ok=False + SLOT_FULL/DUPLICATE nếu không đặt được
    def cancel_appointment(self, session_id: str, appointment_id: int) -> BookingResult: ...


class HandoverService(Protocol):
    def create(self, session_id: str, reason: str, summary: str) -> HandoverResult: ...
    def append(self, session_id: str, reason: str, note: str) -> HandoverResult: ...  # ghi thêm vào ticket đang mở


class AfterSalesService(Protocol):
    def get_history(self, customer_id: int | None, limit: int = 5) -> list[ServiceRecord]: ...


class MaintenanceService(Protocol):
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceResult]: ...
