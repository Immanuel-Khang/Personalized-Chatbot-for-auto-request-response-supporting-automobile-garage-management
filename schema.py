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

class IntentClassificationResult(BaseModel):
    intent: IntentType = Field(description="The primary intent of the customer's latest message.")

StageType = Literal["IDLE", "TU_VAN", "BAO_DUONG", "HOP_DONG", "CHO_DUYET"]

class HarnessState(TypedDict):
    session_id: str
    messages: Annotated[List[AnyMessage], add_messages]
    stage: StageType
    intent: Optional[str]
    pending_discount_id: Optional[str]