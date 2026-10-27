"""
HỢP ĐỒNG HÀM giữa Agent (người B) và các Service (người A, C).
Agent chỉ được gọi các hàm dưới đây, KHÔNG được import trực tiếp DB hay thư viện RAG.
Nhờ vậy mỗi người viết phần của mình độc lập, ghép lại không vỡ.
"""
from typing import Optional, Protocol

from app.contracts.schemas import (
    BookingResult, HandoverResult, KnowledgeChunk,
    MaintenanceResult, Product, ServiceRecord, ServiceType,
)


class ProductService(Protocol):
    def search(self, query: str, limit: int = 3, fallback: bool = True) -> list[Product]: ...
    def find_model(self, name: str, limit: int = 3) -> list[Product]: ...
    def list_vehicles(self, limit: int = 10) -> list[Product]: ...
    def vehicle_summary(self) -> dict[str, int]: ...
    def get_by_id(self, product_id: int) -> Optional[Product]: ...
    def get_by_name(self, name: str) -> Optional[Product]: ...


class KnowledgeService(Protocol):
    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]: ...


class BookingService(Protocol):
    def create_appointment(
        self, session_id: str, service_type: ServiceType, when_text: str, phone: str
    ) -> BookingResult: ...


class HandoverService(Protocol):
    def create(self, session_id: str, reason: str, summary: str) -> HandoverResult: ...
    def append(self, session_id: str, reason: str, note: str) -> HandoverResult: ...  # ghi thêm vào ticket đang mở


class AfterSalesService(Protocol):
    def get_history(self, customer_id: int | None, limit: int = 5) -> list[ServiceRecord]: ...


class MaintenanceService(Protocol):
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceResult]: ...
