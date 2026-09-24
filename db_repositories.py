from abc import ABC, abstractmethod
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from datetime import datetime

# --- Entity Models ---
class Car(BaseModel):
    id: str
    name: str
    car_type: str  # Sedan, SUV, etc.
    price: float   # Giá niêm yết chuẩn
    video_url: Optional[str] = None
    stock: int = 1

class Customer(BaseModel):
    id: Optional[str] = None
    session_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

class DiscountRequest(BaseModel):
    id: str
    session_id: str
    car_id: str
    requested_percent: float
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED
    created_at: datetime = Field(default_factory=datetime.utcnow)

class MaintenanceItem(BaseModel):
    km_milestone: int
    model: str
    tasks: List[str]
    estimated_cost: float

# --- Interfaces (Contracts) ---
class ICarRepository(ABC):
    @abstractmethod
    def search(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[Car]:
        pass

    @abstractmethod
    def get_by_name(self, name: str) -> Optional[Car]:
        pass

class IDiscountRepository(ABC):
    @abstractmethod
    def create_request(self, session_id: str, car_id: str, percent: float) -> DiscountRequest:
        pass

    @abstractmethod
    def get_latest_by_session(self, session_id: str) -> Optional[DiscountRequest]:
        pass

    @abstractmethod
    def update_status(self, request_id: str, status: str) -> bool:
        pass

class IMaintenanceRepository(ABC):
    @abstractmethod
    def get_milestone_tasks(self, model: str, km: int) -> Optional[MaintenanceItem]:
        pass

# --- In-Memory Implementations (Dùng để test, thay thế bằng SQLAlchemy sau) ---
class InMemoryCarRepository(ICarRepository):
    def __init__(self):
        self._cars = [
            Car(id="C01", name="Toyota Vios G", car_type="Sedan", price=592_000_000, video_url="https://youtube.com/vios"),
            Car(id="C02", name="Toyota Corolla Cross", car_type="SUV", price=760_000_000, video_url="https://youtube.com/cross"),
            Car(id="C03", name="Toyota Camry 2.5Q", car_type="Sedan", price=1_405_000_000),
        ]

    def search(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[Car]:
        results = self._cars
        if keyword:
            results = [c for c in results if keyword.lower() in c.name.lower() or keyword.lower() in c.car_type.lower()]
        if max_price:
            results = [c for c in results if c.price <= max_price]
        return results

    def get_by_name(self, name: str) -> Optional[Car]:
        for c in self._cars:
            if name.lower() in c.name.lower():
                return c
        return None

class InMemoryDiscountRepository(IDiscountRepository):
    def __init__(self):
        self._db: Dict[str, DiscountRequest] = {}
        self._counter = 1

    def create_request(self, session_id: str, car_id: str, percent: float) -> DiscountRequest:
        req_id = f"DISC-{self._counter:04d}"
        self._counter += 1
        req = DiscountRequest(id=req_id, session_id=session_id, car_id=car_id, requested_percent=percent)
        self._db[req_id] = req
        return req

    def get_latest_by_session(self, session_id: str) -> Optional[DiscountRequest]:
        matches = [r for r in self._db.values() if r.session_id == session_id]
        return matches[-1] if matches else None

    def update_status(self, request_id: str, status: str) -> bool:
        if request_id in self._db:
            self._db[request_id].status = status
            return True
        return False

class InMemoryMaintenanceRepository(IMaintenanceRepository):
    def get_milestone_tasks(self, model: str, km: int) -> Optional[MaintenanceItem]:
        # Quy chuẩn các mốc: 5000, 10000, 20000 km...
        milestone = min([5000, 10000, 20000, 40000], key=lambda x: abs(x - km))
        return MaintenanceItem(
            km_milestone=milestone,
            model=model,
            tasks=["Thay dầu máy", "Kiểm tra lọc gió", "Siết ốc gầm", "Kiểm tra phanh"],
            estimated_cost=1_200_000 if milestone <= 10000 else 3_500_000
        )

# Dependency Injection Container Đơn giản
class DatabaseContext:
    cars: ICarRepository = InMemoryCarRepository()
    discounts: IDiscountRepository = InMemoryDiscountRepository()
    maintenance: IMaintenanceRepository = InMemoryMaintenanceRepository()

db = DatabaseContext()