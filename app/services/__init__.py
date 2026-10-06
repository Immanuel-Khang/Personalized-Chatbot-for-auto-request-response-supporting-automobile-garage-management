"""
Điểm duy nhất để agent lấy service. Test có thể gọi set_services() để thay bằng bản giả (mock).
"""
from dataclasses import dataclass

from app.contracts.interfaces import (
    BookingService, HandoverService,
    KnowledgeService, MaintenanceService, ProductService,
)
from app.services.booking import DbBookingService
from app.services.handover import DbHandoverService
from app.services.knowledge import RagKnowledgeService
from app.services.maintenance import DbMaintenanceService
from app.services.product import DbProductService


@dataclass
class Services:
    product: ProductService
    knowledge: KnowledgeService
    booking: BookingService
    handover: HandoverService
    maintenance: MaintenanceService


_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services(
            product=DbProductService(), knowledge=RagKnowledgeService(),
            booking=DbBookingService(), handover=DbHandoverService(),
            maintenance=DbMaintenanceService(),
        )
    return _services


def set_services(s: Services | None) -> None:
    global _services
    _services = s
