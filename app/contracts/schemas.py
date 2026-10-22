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
    AFTER_SALES = "AFTER_SALES"
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
    SHOWROOM_VISIT = "SHOWROOM_VISIT"


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
    service_type: Optional[str] = Field(default=None, description="Loại dịch vụ: MAINTENANCE | REPAIR | TEST_DRIVE | SHOWROOM_VISIT")
    appointment_when: Optional[str] = Field(default=None, description="Thời điểm hẹn (VD: sáng thứ 3, ngày mai, 15/10)")
    payload_tons: Optional[float] = Field(default=None, description="Tải trọng hàng khách cần chở (tấn), chỉ khi khách nói rõ")


class UseCategory(str, Enum):
    """Nhóm mục đích dùng xe: chỉ để chọn cách trả lời, KHÔNG dùng để xếp hạng xe."""
    CARGO = "CARGO"  # chở hàng, vật liệu, kinh doanh vận tải
    PASSENGER = "PASSENGER"  # gia đình, đi lại, chở người
    OTHER = "OTHER"


class SalesField(str, Enum):
    """Yêu cầu mua xe khách có thể yêu cầu xóa (VD 'không giới hạn ngân sách nữa')."""
    INTENDED_USE = "intended_use"
    BUDGET = "budget_vnd"
    CAR_MODEL = "car_model"
    PAYLOAD = "payload_tons"


class SalesSignals(BaseModel):
    """Diễn giải hội thoại mua xe của LLM. Code kiểm tra lại trước khi ghi vào slots (preprocess.merge_sales_slots)."""
    answers_pending_question: bool = Field(
        default=False, description="Tin nhắn là câu trả lời cho câu bot vừa hỏi (chỉ khi có câu hỏi đang chờ)")
    catalog_request: bool = Field(
        default=False, description="Khách yêu cầu RÕ RÀNG xem danh sách/tất cả mẫu xe; 'muốn mua xe' KHÔNG phải")
    intended_use_stated: bool = Field(
        default=False, description="Khách TỰ NÓI mục đích dùng xe trong tin nhắn này (không phải bạn suy đoán)")
    use_category: Optional[UseCategory] = Field(default=None, description="Nhóm của mục đích khách vừa nói")
    uncertain: bool = Field(default=False, description="Khách nói chưa biết / chưa rõ nhu cầu")
    accepts_offer: Optional[bool] = Field(
        default=None, description="Bot vừa hỏi có/không: true = khách đồng ý, false = từ chối, null = không phải trả lời có/không")
    cleared_fields: list[SalesField] = Field(
        default_factory=list, description="Yêu cầu khách nói rõ muốn bỏ (VD bỏ giới hạn ngân sách)")


class IntentTask(BaseModel):
    """Một việc trong tin nhắn nhiều ý: intent + đúng phần câu của khách thuộc intent đó."""
    intent: Intent
    text: str = Field(description="Trích nguyên văn phần câu của khách thuộc intent này")


class IntentAndSlotExtraction(BaseModel):
    """Kết quả LLM structured output: tất cả intent, từng việc tách theo vế câu + slots."""
    intents: list[Intent] = Field(description="Tất cả intent trong tin nhắn mới nhất, ý định chính đứng trước")
    tasks: list[IntentTask] = Field(default_factory=list,
                                    description="Mỗi intent kèm phần câu tương ứng, theo thứ tự trong tin nhắn")
    extracted_slots: CustomerSlots = Field(default_factory=CustomerSlots)
    sales: SalesSignals = Field(default_factory=SalesSignals)


# ---- API ----
class ChatRequest(BaseModel):
    # UUID trình duyệt tạo và giữ trong localStorage = chat_sessions.id. Chỉ nhận ra phiên, không chứng minh danh tính.
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    message: str


class ChatResponse(BaseModel):
    session_id: str
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


class ServiceRecord(BaseModel):
    id: int
    service_date: str
    service_type: str
    description: str
    cost_vnd: int


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


class MaintenanceResult(BaseModel):
    """Kết quả tra cứu bảo dưỡng theo mốc km."""
    km_milestone: int
    model_name: str
    tasks: list[str]
    estimated_cost: float
