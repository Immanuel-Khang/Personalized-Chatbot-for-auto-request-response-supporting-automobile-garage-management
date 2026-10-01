"""
Dummy In-Memory Database Layer.
All repositories implement abstract interfaces (ABC) so they can be swapped
for real SQLAlchemy / AsyncPG implementations without touching agent code.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class CustomerEntity(BaseModel):
    id: str
    session_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class CarEntity(BaseModel):
    id: str
    name: str
    category: str
    price: float
    video_url: Optional[str] = None
    
class DiscountRequestEntity(BaseModel):
    id: str
    session_id: str
    car_name: str
    discount_percent: float
    status: str = "PENDING"          # PENDING | APPROVED | REJECTED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class AppointmentEntity(BaseModel):
    id: str
    customer_id: Optional[str]       # Links to customers table
    session_id: str
    car_model: str
    appointment_date: str
    appointment_time: str
    service_type: str                # MAINTENANCE | REPAIR | TEST_DRIVE | WARRANTY_CHECK
    status: str = "PENDING_ADMIN"    # CONFIRMED | PENDING_ADMIN
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class LeadEntity(BaseModel):
    id: str
    session_id: str
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    car_model: Optional[str] = None
    address: Optional[str] = None
    lead_type: str = "PRICE_QUOTE"   # PRICE_QUOTE | TEST_DRIVE
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
class WorkshopCapacityEntity(BaseModel):
    date_str: str      # "2026-10-03"
    time_slot: str     # "10:00"
    booked_count: int = 0
    max_capacity: int = 4
    
class MaintenanceItemEntity(BaseModel):
    km_milestone: int
    model_name: str
    tasks: List[str]
    estimated_cost: float

# ═══════════════════════════════════════════════════════════════
# REPOSITORY INTERFACES (Contracts for future real DB swap)
# ═══════════════════════════════════════════════════════════════
class ICustomerRepository(ABC):
    @abstractmethod
    def get_by_session(self, session_id: str) -> Optional[CustomerEntity]: pass
    
    @abstractmethod
    def get_by_id(self, customer_id: str) -> Optional[CustomerEntity]: pass
    
    @abstractmethod
    def create(self, 
               session_id: str, 
               name: Optional[str] = None,
               phone: Optional[str] = None, 
               address: Optional[str] = None
    ) -> CustomerEntity: pass
    
    @abstractmethod
    def update(self, customer_id: str, **kwargs) -> Optional[CustomerEntity]: pass
    
    @abstractmethod
    def get_by_phone(self, phone: str) -> Optional[CustomerEntity]: pass
    
class ICarRepository(ABC):
    @abstractmethod
    def search(self, 
               keyword: Optional[str] = None, 
               max_price: Optional[float] = None
    ) -> List[CarEntity]: pass
    
    @abstractmethod
    def get_by_name(self, name: str) -> Optional[CarEntity]: pass
    
class IDiscountRepository(ABC):
    @abstractmethod
    def create_request(self, session_id: str, car_name: str, percent: float) -> DiscountRequestEntity: pass
    
    @abstractmethod
    def get_by_id(self, request_id: str) -> Optional[DiscountRequestEntity]: pass
    
    @abstractmethod
    def update_status(self, request_id: str, status: str) -> bool: pass
    
    @abstractmethod
    def update(self, request_id: str, **updates) -> bool: pass
    
class IAppointmentRepository(ABC):
    @abstractmethod
    def create(self, customer_id: Optional[str], session_id: str, car_model: str,
               date: str, time: str, service_type: str, status: str) -> AppointmentEntity: pass
    
    @abstractmethod
    def count_by_slot(self, date: str, time: str) -> int: pass
    
    @abstractmethod
    def get_by_session(self, session_id: str) -> List[AppointmentEntity]: pass
    
class ILeadRepository(ABC):
    @abstractmethod
    def create(self, session_id: str, customer_name: Optional[str],
               phone: Optional[str], car_model: Optional[str],
               address: Optional[str], lead_type: str) -> LeadEntity: pass
    
class IMaintenanceRepository(ABC):
    @abstractmethod
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceItemEntity]: pass
    
# ═══════════════════════════════════════════════════════════════
# IN-MEMORY IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════
class InMemoryCustomerRepository(ICustomerRepository):
    def __init__(self):
        self._store: Dict[str, CustomerEntity] = {}
        self._seq = 1
        
    def get_by_session(self, session_id: str) -> Optional[CustomerEntity]:
        for c in self._store.values():
            if c.session_id == session_id:
                return c
        return None
    
    def get_by_id(self, customer_id: str) -> Optional[CustomerEntity]:
        return self._store.get(customer_id)
    
    def create(self, session_id: str, name=None, phone=None, address=None) -> CustomerEntity:
        cid = f"CUST-{self._seq:04d}"
        self._seq += 1
        entity = CustomerEntity(id=cid, session_id=session_id, name=name, phone=phone, address=address)
        self._store[cid] = entity
        return entity
    
    def get_by_phone(self, phone: str) -> Optional[CustomerEntity]:
        return next(
            (
                customer
                for customer in self._store.values()
                if customer.phone == phone
            ),
            None
        )
    
    def update(self, customer_id: str, **kwargs) -> Optional[CustomerEntity]:
        entity = self._store.get(customer_id)
        if not entity:
            return None
        updated = entity.model_copy(update=kwargs)
        self._store[customer_id] = updated
        return updated
    
class InMemoryCarRepository(ICarRepository):
    def __init__(self):
        self._cars: List[CarEntity] = [
            CarEntity(id="CAR-01", name="Toyota Vios G", category="Sedan", price=592_000_000, video_url="https://youtube.com/watch?v=vios"),
            CarEntity(id="CAR-02", name="Toyota Corolla Cross", category="SUV", price=760_000_000, video_url="https://youtube.com/watch?v=cross"),
            CarEntity(id="CAR-03", name="Toyota Camry 2.5Q", category="Sedan", price=1_405_000_000),
            CarEntity(id="CAR-04", name="Foton Auman C160", category="Truck", price=720_000_000),
            CarEntity(id="CAR-05", name="Foton Ollin 700B", category="Truck", price=480_000_000),
        ]
        
    def search(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[CarEntity]:
        results = self._cars
        if keyword:
            kw = keyword.lower()
            results = [c for c in results if kw in c.name.lower() or kw in c.category.lower()]
        if max_price and max_price > 0:
            results = [c for c in results if c.price <= max_price]
        return results
    
    def get_by_name(self, name: str) -> Optional[CarEntity]:
        name_lower = name.lower()
        # Exact substring match first
        for car in self._cars:
            if name_lower in car.name.lower():
                return car
        # Token-based fallback
        tokens = [t for t in name_lower.split() if len(t) > 2]
        for car in self._cars:
            if any(token in car.name.lower() for token in tokens):
                return car
        return None
    
class InMemoryDiscountRepository(IDiscountRepository):
    def __init__(self):
        self._store: Dict[str, DiscountRequestEntity] = {}
        self._seq = 1
        
    def create_request(self, session_id: str, car_name: str, percent: float) -> DiscountRequestEntity:
        rid = f"REQ-DISC-{self._seq:04d}"
        self._seq += 1
        entity = DiscountRequestEntity(id=rid, session_id=session_id, car_name=car_name, discount_percent=percent)
        self._store[rid] = entity
        return entity
    
    def get_by_id(self, request_id: str) -> Optional[DiscountRequestEntity]:
        return self._store.get(request_id)
    
    def update_status(self, request_id: str, status: str) -> bool:
        if request_id in self._store:
            self._store[request_id] = self._store[request_id].model_copy(update={"status": status})
            return True
        return False
    
    def update(self, request_id: str, **updates) -> bool:
        entity = self._store.get(request_id)

        if entity is None:
            return False

        self._store[request_id] = entity.model_copy(
            update=updates
        )
        
        return True
    
class InMemoryAppointmentRepository(IAppointmentRepository):
    def __init__(self):
        self._store: Dict[str, AppointmentEntity] = {}
        self._seq = 1
        
    def create(self, customer_id, session_id, car_model, date, time, service_type, status) -> AppointmentEntity:
        aid = f"APT-{self._seq:04d}"
        self._seq += 1
        entity = AppointmentEntity(
            id=aid, customer_id=customer_id, session_id=session_id,
            car_model=car_model, appointment_date=date, appointment_time=time,
            service_type=service_type, status=status
        )
        self._store[aid] = entity
        return entity
    
    def count_by_slot(self, date: str, time: str) -> int:
        return sum(
            1 for a in self._store.values()
            if a.appointment_date == date and a.appointment_time == time
            and a.status == "CONFIRMED"
        )
        
    def get_by_session(self, session_id: str) -> List[AppointmentEntity]:
        print(self._store)
        return [a for a in self._store.values() if a.session_id == session_id]

class InMemoryLeadRepository(ILeadRepository):
    def __init__(self):
        self._store: Dict[str, LeadEntity] = {}
        self._seq = 1
        
    def create(self, session_id, customer_name, phone, car_model, address, lead_type) -> LeadEntity:
        lid = f"LEAD-{self._seq:04d}"
        self._seq += 1
        entity = LeadEntity(
            id=lid, session_id=session_id, customer_name=customer_name,
            phone=phone, car_model=car_model, address=address, lead_type=lead_type
        )
        self._store[lid] = entity
        return entity

class InMemoryMaintenanceRepository(IMaintenanceRepository):
    _MILESTONE_DATA = {
        5_000:  (["Thay dầu máy và lọc dầu", "Kiểm tra lọc gió", "Kiểm tra áp suất lốp"], 1_200_000),
        10_000: (["Thay dầu máy và lọc dầu", "Vệ sinh lọc gió điều hòa", "Kiểm tra phanh 4 bánh", "Đảo lốp"], 1_800_000),
        20_000: (["Thay dầu máy", "Thay lọc gió động cơ", "Thay dầu hộp số", "Kiểm tra hệ thống làm mát", "Siết ốc gầm"], 3_500_000),
        40_000: (["Thay bugi", "Thay dầu máy + lọc", "Thay dầu trợ lực", "Kiểm tra dây curoa", "Kiểm tra phanh đầy đủ"], 5_800_000),
    }
    
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceItemEntity]:
        milestones = sorted(self._MILESTONE_DATA.keys())
        nearest = min(milestones, key=lambda x: abs(x - km))
        tasks, cost = self._MILESTONE_DATA[nearest]
        return MaintenanceItemEntity(
            km_milestone=nearest, model_name=model, tasks=tasks, estimated_cost=cost
        )
        
# ═══════════════════════════════════════════════════════════════
# DEPENDENCY INJECTION CONTAINER (Global Singleton)
# ═══════════════════════════════════════════════════════════════

class DatabaseContainer:
    """
    Global container for all repositories.
    To swap to a real database: replace any InMemory* class with your SQLAlchemy implementation.
    Example:
        container.cars = SqlAlchemyCarRepository(async_session_factory)
    """
    def __init__(self):
        self.customers: ICustomerRepository = InMemoryCustomerRepository()
        self.cars: ICarRepository = InMemoryCarRepository()
        self.discounts: IDiscountRepository = InMemoryDiscountRepository()
        self.appointments: IAppointmentRepository = InMemoryAppointmentRepository()
        self.leads: ILeadRepository = InMemoryLeadRepository()
        self.maintenance: IMaintenanceRepository = InMemoryMaintenanceRepository()
        self.workshop_max_capacity: int = 4   # Max cars per time slot
        self.unanswered_logs: List[str] = []  # Log list for out-of-FAQ / unanswered questions

        
# Single global instance — import `db` everywhere
db = DatabaseContainer()
