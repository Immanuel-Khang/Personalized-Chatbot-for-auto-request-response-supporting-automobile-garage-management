"""
HỢP ĐỒNG DỮ LIỆU giữa các module.
QUY TẮC: sửa file này phải được CẢ 3 người đồng ý (mở PR, 2 người approve).
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    SALES = "SALES"
    APPOINTMENT = "APPOINTMENT"
    KNOWLEDGE = "KNOWLEDGE"
    CONTRACT = "CONTRACT"
    DISCOUNT = "DISCOUNT"
    MAINTENANCE = "MAINTENANCE"
    GREETING = "GREETING"
    OTHER = "OTHER"


class ConversationMode(str, Enum):
    BOT = "BOT"
    HUMAN_PENDING = "HUMAN_PENDING"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"


class ServiceType(str, Enum):
    MAINTENANCE = "MAINTENANCE"
    REPAIR = "REPAIR"
    TEST_DRIVE = "TEST_DRIVE"


# ---- Slot-filling: thông tin khách hàng thu thập dần qua nhiều lượt ----
class CustomerSlots(BaseModel):
    """Dùng cho LLM structured output: trích xuất thông tin từ tin nhắn khách."""
    car_model: Optional[str] = Field(default=None, description="Dòng xe khách đang quan tâm (VD: Vios, Cross, Camry)")
    budget_vnd: Optional[float] = Field(default=None, description="Ngân sách tối đa của khách")
    intended_use: Optional[str] = Field(default=None, description="Mục đích sử dụng: Gia đình, dịch vụ, đi làm...")
    current_mileage_km: Optional[int] = Field(default=None, description="Số km hiện tại của xe")
    customer_name: Optional[str] = Field(default=None, description="Họ tên khách hàng")
    customer_phone: Optional[str] = Field(default=None, description="Số điện thoại liên hệ")
    customer_address: Optional[str] = Field(default=None, description="Địa chỉ nơi ở hoặc địa chỉ nhận xe")
    decided_price: Optional[float] = Field(default=None, description="Mức giá cuối cùng khách chốt mua (VNĐ)")
    service_type: Optional[str] = Field(default=None, description="Loại dịch vụ: MAINTENANCE | REPAIR | TEST_DRIVE")
    appointment_when: Optional[str] = Field(default=None, description="Thời điểm hẹn (VD: sáng thứ 3, ngày mai, 15/10)")


class IntentAndSlotExtraction(BaseModel):
    """Kết quả LLM structured output: intent + slots."""
    intent: Intent = Field(description="Intent chính từ tin nhắn mới nhất của khách")
    extracted_slots: CustomerSlots = Field(default_factory=CustomerSlots)


# ---- API ----
class ChatRequest(BaseModel):
    visitor_token: str
    message: str
    conversation_id: int | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    mode: ConversationMode
    message_id: int | None = None  # id tin bot vừa gửi (client dùng để poll tin nhân viên/hệ thống sau đó)
    trace: list[str] = []  # các node đã đi qua - dùng để debug và để chấm eval theo trajectory


# ---- Dữ liệu trả về từ các service ----
class Product(BaseModel):
    id: int
    kind: str  # CAR | PART
    name: str
    list_price_vnd: int
    price_as_of: str  # ngày hiệu lực của giá, bắt buộc phải hiển thị khi báo giá
    specs: dict = {}


class KnowledgeChunk(BaseModel):
    text: str
    source: str
    score: float


class BookingResult(BaseModel):
    ok: bool
    appointment_id: int | None = None
    status: str = ""  # CONFIRMED | PENDING_APPROVAL | REJECTED
    message: str = ""


class HandoverResult(BaseModel):
    task_id: int
    lead_id: int | None = None


class MaintenanceResult(BaseModel):
    """Kết quả tra cứu bảo dưỡng theo mốc km."""
    km_milestone: int
    model_name: str
    tasks: list[str]
    estimated_cost: float
