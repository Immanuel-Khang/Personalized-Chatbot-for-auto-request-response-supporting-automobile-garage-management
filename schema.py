from enum import Enum
from typing import Annotated, List, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
# ─────────────────────────────────────────
# 1. INTENT TYPE ENUM
# ─────────────────────────────────────────
class IntentType(str, Enum):
    SALES = "SALES"
    MAINTENANCE = "MAINTENANCE"
    APPOINTMENT = "APPOINTMENT"
    CONTRACT = "CONTRACT"
    DISCOUNT = "DISCOUNT"
    KNOWLEDGE_FAQ = "KNOWLEDGE_FAQ"
    GENERAL = "GENERAL"

# extract necessary information from the conversation
from typing import Optional
from pydantic import BaseModel, Field


class CustomerSlots(BaseModel):
    """Structured memory of information extracted from the conversation."""

    # ============================================================
    # Customer information
    # ============================================================

    customer_name: Optional[str] = Field(
        default=None,
        description="Full name of the customer"
    )

    customer_phone: Optional[str] = Field(
        default=None,
        description="Customer phone number"
    )

    customer_address: Optional[str] = Field(
        default=None,
        description="Customer address, province, or city"
    )

    # ============================================================
    # Sales information
    # ============================================================

    car_model: Optional[str] = Field(
        default=None,
        description=(
            "Specific car model the customer is interested in "
            "(e.g. 'Vios G', 'Corolla Cross', 'Foton Auman')"
        )
    )

    budget_vnd: Optional[float] = Field(
        default=None,
        description=(
            "Maximum budget in VND. Convert natural language amounts "
            "to VND, e.g. 'tầm 600 triệu' -> 600000000"
        )
    )

    intended_use: Optional[str] = Field(
        default=None,
        description=(
            "Customer's intended use for the vehicle, "
            "e.g. gia đình, vận tải, đi làm"
        )
    )

    decided_price: Optional[float] = Field(
        default=None,
        description=(
            "Final agreed purchase price in VND"
        )
    )

    # ============================================================
    # Maintenance information
    # ============================================================

    current_mileage_km: Optional[int] = Field(
        default=None,
        description=(
            "Current vehicle mileage in kilometers, "
            "used for maintenance queries"
        )
    )

    # ============================================================
    # Appointment information
    # ============================================================

    # Appointment
    appointment_date: Optional[str] = Field(
        default=None,
        description=(
            "Explicit calendar date only. "
            "Examples: 'ngày 6 tháng 10', '06/10/2026'. "
            "Normalize to YYYY-MM-DD. "
            "DO NOT use this field for weekdays such as 'thứ 6'."
        )
    )

    appointment_weekday: Optional[str] = Field(
        default=None,
        description=(
            "Weekday requested by the customer. "
            "Vietnamese 'thứ 2' = MONDAY, "
            "'thứ 3' = TUESDAY, "
            "'thứ 4' = WEDNESDAY, "
            "'thứ 5' = THURSDAY, "
            "'thứ 6' = FRIDAY, "
            "'thứ 7' = SATURDAY, "
            "'chủ nhật' = SUNDAY."
        )
    )

    appointment_time: Optional[str] = Field(
        default=None,
        description=(
            "Time requested for service appointment. "
            "Normalize times such as '10 giờ sáng' to '10:00'"
        )
    )

    service_type: Optional[str] = Field(
        default=None,
        description=(
            "Type of service appointment. "
            "Allowed values: MAINTENANCE, REPAIR, "
            "TEST_DRIVE, WARRANTY_CHECK"
        )
    )

# extract slots and intents
class IntentAndSlotExtraction(BaseModel):
    intent: IntentType = Field(description="Primary intent of the customer's latest message.")
    extracted_slots: CustomerSlots = Field(
        description="Any slot values found in the message. Leave fields as null if not mentioned."
    )

class HarnessState(TypedDict):
    session_id: str                                         # Web/Zalo session identifier
    messages: Annotated[List[AnyMessage], add_messages]     # Full conversation history
    stage: str                                              # Plain str: IDLE, TU_VAN, BAO_DUONG, DAT_LICH, HOP_DONG, CHO_DUYET, KNOWLEDGE
    intent: Optional[str]                                   # Latest classified intent
    slots: dict                                             # Plain dict to avoid LangGraph deserialization warnings
    pending_discount_id: Optional[str]                      # Active discount ticket awaiting manager approval
    customer_id: Optional[str]                              # Resolved customer DB id for session continuity