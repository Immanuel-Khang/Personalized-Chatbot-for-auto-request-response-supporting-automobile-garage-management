from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Literal
from pydantic import BaseModel, Field
from datetime import datetime

# --- Domain Entities ---
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
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class MaintenanceItemEntity(BaseModel):
    km_milestone: int
    model_name: str
    tasks: List[str]
    estimated_cost: float

# --- Repository Interfaces ---
class ICarRepository(ABC):
    @abstractmethod
    def search_cars(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[CarEntity]:
        pass

    @abstractmethod
    def get_car_by_name(self, name: str) -> Optional[CarEntity]:
        pass

class IDiscountRepository(ABC):
    @abstractmethod
    def create_request(self, session_id: str, car_name: str, percent: float) -> DiscountRequestEntity:
        pass

    @abstractmethod
    def get_by_id(self, request_id: str) -> Optional[DiscountRequestEntity]:
        pass

    @abstractmethod
    def update_status(self, request_id: str, status: Literal["APPROVED", "REJECTED"]) -> bool:
        pass

class IMaintenanceRepository(ABC):
    @abstractmethod
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceItemEntity]:
        pass

# --- Mock In-Memory Implementations (To be swapped with SQL later) ---
class InMemoryCarRepository(ICarRepository):
    def __init__(self):
        self._cars = [
            CarEntity(id="CAR-01", name="Toyota Vios G", category="Sedan", price=592_000_000, video_url="https://youtube.com/vios"),
            CarEntity(id="CAR-02", name="Toyota Corolla Cross", category="SUV", price=760_000_000, video_url="https://youtube.com/cross"),
            CarEntity(id="CAR-03", name="Toyota Camry 2.5Q", category="Sedan", price=1_405_000_000),
        ]

    def search_cars(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[CarEntity]:
        results = self._cars
        if keyword:
            results = [c for c in results if keyword.lower() in c.name.lower() or keyword.lower() in c.category.lower()]
        if max_price:
            results = [c for c in results if c.price <= max_price]
        return results

    def get_car_by_name(self, name: str) -> Optional[CarEntity]:
        for car in self._cars:
            if name.lower() in car.name.lower():
                return car
        return None

class InMemoryDiscountRepository(IDiscountRepository):
    def __init__(self):
        self._store: Dict[str, DiscountRequestEntity] = {}
        self._seq = 1

    def create_request(self, session_id: str, car_name: str, percent: float) -> DiscountRequestEntity:
        req_id = f"REQ-DISC-{self._seq:04d}"
        self._seq += 1
        entity = DiscountRequestEntity(id=req_id, session_id=session_id, car_name=car_name, discount_percent=percent)
        self._store[req_id] = entity
        return entity

    def get_by_id(self, request_id: str) -> Optional[DiscountRequestEntity]:
        return self._store.get(request_id)

    def update_status(self, request_id: str, status: Literal["APPROVED", "REJECTED"]) -> bool:
        if request_id in self._store:
            self._store[request_id].status = status
            return True
        return False

class InMemoryMaintenanceRepository(IMaintenanceRepository):
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceItemEntity]:
        milestone = min([5000, 10000, 20000, 40000], key=lambda x: abs(x - km))
        return MaintenanceItemEntity(
            km_milestone=milestone,
            model_name=model,
            tasks=["Oil & filter change", "Tire rotation", "Brake system inspection"],
            estimated_cost=1_500_000 if milestone <= 10000 else 3_800_000
        )

# Global Container for Dependency Injection
class DatabaseContainer:
    cars: ICarRepository = InMemoryCarRepository()
    discounts: IDiscountRepository = InMemoryDiscountRepository()
    maintenance: IMaintenanceRepository = InMemoryMaintenanceRepository()

db_container = DatabaseContainer()