import os
from abc import ABC, abstractmethod
from typing import Optional, List, Dict
from contextlib import contextmanager

from pydantic import BaseModel, Field
from datetime import datetime

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv

load_dotenv()

# --- Entity Models (giữ nguyên field so với bản in-memory cũ) ---

class Car(BaseModel):
    id: str
    name: str
    car_type: str
    price: float
    video_url: Optional[str] = None
    stock: int = 1


class Customer(BaseModel):
    id: Optional[str] = None
    session_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    owned_car_model: Optional[str] = None
    owned_car_km: Optional[int] = None
    budget: Optional[float] = None


class DiscountRequest(BaseModel):
    id: str
    session_id: str
    car_id: str
    requested_percent: float
    status: str = "PENDING"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MaintenanceItem(BaseModel):
    km_milestone: int
    model: str
    tasks: List[str]
    estimated_cost: float


# --- Interfaces (Contracts) - KHÔNG đổi so với bản cũ để tools.py/agent_nodes.py không phải sửa ---

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


class ICustomerRepository(ABC):
    """Chưa được tools.py/agent_nodes.py dùng ở Việc 1 này.
    Để sẵn cho bước tiếp theo (nạp hồ sơ khách vào context)."""

    @abstractmethod
    def get_or_create(self, session_id: str) -> Customer:
        pass

    @abstractmethod
    def update_profile(self, session_id: str, **fields) -> Optional[Customer]:
        pass


# --- Kết nối Postgres: 1 connection pool dùng chung cho cả app (FastAPI nhiều request cùng lúc) ---

_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
_DB_NAME = os.getenv("POSTGRES_DB", "garage_db")
_DB_USER = os.getenv("POSTGRES_USER", "garage_admin")
_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "garage_pass")

_pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    host=_DB_HOST,
    port=_DB_PORT,
    dbname=_DB_NAME,
    user=_DB_USER,
    password=_DB_PASSWORD,
)


@contextmanager
def get_cursor(commit: bool = False):
    """Lấy connection từ pool, tự trả lại pool sau khi dùng xong.
    commit=True cho các câu lệnh INSERT/UPDATE."""
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# --- Postgres Implementations ---

class PostgresCarRepository(ICarRepository):
    def search(self, keyword: Optional[str] = None, max_price: Optional[float] = None) -> List[Car]:
        query = "SELECT id, name, car_type, price, video_url, stock FROM vehicle_models WHERE 1=1"
        params: list = []
        if keyword:
            query += " AND (name ILIKE %s OR car_type ILIKE %s)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        if max_price:
            query += " AND price <= %s"
            params.append(max_price)
        with get_cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [Car(**row) for row in rows]

    def get_by_name(self, name: str) -> Optional[Car]:
        with get_cursor() as cur:
            cur.execute(
                "SELECT id, name, car_type, price, video_url, stock FROM vehicle_models "
                "WHERE name ILIKE %s ORDER BY name LIMIT 1",
                [f"%{name}%"],
            )
            row = cur.fetchone()
        return Car(**row) if row else None


class PostgresDiscountRepository(IDiscountRepository):
    def __init__(self):
        # counter đơn giản để sinh mã DISC-xxxx giống bản cũ, lấy max hiện có trong bảng
        pass

    def _next_id(self, cur) -> str:
        cur.execute(
            "SELECT id FROM escalations WHERE id LIKE 'DISC-%' ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return "DISC-0001"
        last_num = int(row["id"].split("-")[1])
        return f"DISC-{last_num + 1:04d}"

    def create_request(self, session_id: str, car_id: str, percent: float) -> DiscountRequest:
        with get_cursor(commit=True) as cur:
            req_id = self._next_id(cur)
            cur.execute(
                """
                INSERT INTO escalations (id, session_id, car_id, requested_percent, status)
                VALUES (%s, %s, %s, %s, 'PENDING')
                RETURNING id, session_id, car_id, requested_percent, status, created_at
                """,
                [req_id, session_id, car_id, percent],
            )
            row = cur.fetchone()
        return DiscountRequest(**row)

    def get_latest_by_session(self, session_id: str) -> Optional[DiscountRequest]:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT id, session_id, car_id, requested_percent, status, created_at
                FROM escalations WHERE session_id = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                [session_id],
            )
            row = cur.fetchone()
        return DiscountRequest(**row) if row else None

    def update_status(self, request_id: str, status: str) -> bool:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE escalations SET status = %s WHERE id = %s",
                [status, request_id],
            )
            return cur.rowcount > 0


class PostgresMaintenanceRepository(IMaintenanceRepository):
    def get_milestone_tasks(self, model: str, km: int) -> Optional[MaintenanceItem]:
        # Lấy mốc km gần nhất trong DB cho đúng dòng xe (khớp lỏng bằng ILIKE, giống get_by_name)
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT model, km_milestone, tasks, estimated_cost
                FROM maintenance_items
                WHERE model ILIKE %s
                ORDER BY ABS(km_milestone - %s) ASC
                LIMIT 1
                """,
                [f"%{model}%", km],
            )
            row = cur.fetchone()
        if not row:
            return None
        return MaintenanceItem(**row)


class PostgresCustomerRepository(ICustomerRepository):
    def _next_id(self, cur) -> str:
        cur.execute("SELECT id FROM customers WHERE id LIKE 'CUS-%' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return "CUS-0001"
        last_num = int(row["id"].split("-")[1])
        return f"CUS-{last_num + 1:04d}"

    def get_or_create(self, session_id: str) -> Customer:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM customers WHERE session_id = %s", [session_id])
            row = cur.fetchone()
        if row:
            return Customer(**row)
        with get_cursor(commit=True) as cur:
            cust_id = self._next_id(cur)
            cur.execute(
                "INSERT INTO customers (id, session_id) VALUES (%s, %s) RETURNING *",
                [cust_id, session_id],
            )
            row = cur.fetchone()
        return Customer(**row)

    def update_profile(self, session_id: str, **fields) -> Optional[Customer]:
        if not fields:
            return None
        allowed = {"name", "phone", "email", "owned_car_model", "owned_car_km", "budget"}
        set_fields = {k: v for k, v in fields.items() if k in allowed}
        if not set_fields:
            return None
        set_clause = ", ".join(f"{k} = %s" for k in set_fields)
        with get_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE customers SET {set_clause}, updated_at = now() "
                f"WHERE session_id = %s RETURNING *",
                list(set_fields.values()) + [session_id],
            )
            row = cur.fetchone()
        return Customer(**row) if row else None


# --- Dependency Injection Container - CÙNG TÊN BIẾN `db` như bản cũ ---

class DatabaseContext:
    cars: ICarRepository = PostgresCarRepository()
    discounts: IDiscountRepository = PostgresDiscountRepository()
    maintenance: IMaintenanceRepository = PostgresMaintenanceRepository()
    customers: ICustomerRepository = PostgresCustomerRepository()


db = DatabaseContext()
