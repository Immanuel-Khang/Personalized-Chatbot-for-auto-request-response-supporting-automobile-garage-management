from enum import Enum
from typing import Annotated, Literal, Optional, List
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class IntentType(str, Enum):
    SALES = "SALES"
    MAINTENANCE = "MAINTENANCE"
    CONTRACT = "CONTRACT"
    DISCOUNT = "DISCOUNT"
    GENERAL = "GENERAL"
    
# serve slot_filling purpose
class CustomerSlots(BaseModel): 
    # Dữ liệu tư vấn bán xe
    car_model: Optional[str] = Field(default=None, description="Dòng xe khách đang quan tâm (VD: Vios, Cross, Camry)")
    budget_vnd: Optional[float] = Field(default=None, description="Ngân sách tối đa của khách")
    intended_use: Optional[str] = Field(default=None, description="Mục đích sử dụng: Gia đình, dịch vụ, đi làm...")
    # Dữ liệu bảo dưỡng
    current_mileage_km: Optional[int] = Field(default=None, description="Số km hiện tại của xe")
    # Dữ liệu khách hàng chốt hợp đồng
    customer_name: Optional[str] = Field(default=None, description="Họ tên khách hàng")
    customer_phone: Optional[str] = Field(default=None, description="Số điện thoại liên hệ")

class IntentAndSlotExtraction(BaseModel):
    intent: IntentType = Field(description="The primary intent of the customer's latest message.")
    extracted_slots: CustomerSlots

class HarnessState(TypedDict):
    session_id: str
    messages: Annotated[List[AnyMessage], add_messages]
    stage: Literal["IDLE", "TU_VAN", "BAO_DUONG", "HOP_DONG", "CHO_DUYET"]
    intent: Optional[str]
    pending_discount_id: Optional[str]
    slots: CustomerSlots