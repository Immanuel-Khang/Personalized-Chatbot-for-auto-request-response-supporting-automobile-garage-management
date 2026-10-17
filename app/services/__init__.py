"""
Điểm duy nhất để agent lấy service. Test có thể gọi set_services() để thay bằng bản giả (mock).
"""
from dataclasses import dataclass, field

from app.contracts.interfaces import (
    AfterSalesService, BookingService, HandoverService,
    KnowledgeService, MaintenanceService, ProductService, ProfileService,
)
from app.services.after_sales import DbAfterSalesService
from app.services.booking import DbBookingService
from app.services.handover import DbHandoverService
from app.services.knowledge import RagKnowledgeService
from app.services.maintenance import DbMaintenanceService
from app.services.product import DbProductService
from app.services.profile import DbProfileService


@dataclass
class Services:
    product: DbProductService
    knowledge: RagKnowledgeService
    booking: DbBookingService
    handover: DbHandoverService
    maintenance: DbMaintenanceService
    profile: DbProfileService
    after_sales: AfterSalesService = field(default_factory=DbAfterSalesService)

_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services(
            product=DbProductService(), knowledge=RagKnowledgeService(),
            booking=DbBookingService(), handover=DbHandoverService(),
            maintenance=DbMaintenanceService(), profile=DbProfileService(),
        )
    return _services


def set_services(s: Services | None) -> None:
    global _services
    _services = s
