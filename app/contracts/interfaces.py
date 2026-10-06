"""
HỢP ĐỒNG HÀM giữa Agent (người B) và các Service (người A, C).
Agent chỉ được gọi các hàm dưới đây, KHÔNG được import trực tiếp DB hay thư viện RAG.
Nhờ vậy mỗi người viết phần của mình độc lập, ghép lại không vỡ.
"""
from typing import Optional, Protocol

from app.contracts.schemas import (
    BookingResult, HandoverResult, KnowledgeChunk,
    MaintenanceResult, Product, ServiceType,
)


class ProductService(Protocol):
    def search(self, query: str, limit: int = 3) -> list[Product]: ...
    def get_by_name(self, name: str) -> Optional[Product]: ...


class KnowledgeService(Protocol):
    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]: ...


class BookingService(Protocol):
    def create_appointment(
        self, customer_id: int, service_type: ServiceType, when_text: str, phone: str
    ) -> BookingResult: ...


class HandoverService(Protocol):
    def create(self, conversation_id: int, reason: str, summary: str) -> HandoverResult: ...
    def append(self, conversation_id: int, reason: str, note: str) -> HandoverResult: ...  # ghi thêm vào ticket đang mở


class MaintenanceService(Protocol):
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceResult]: ...
