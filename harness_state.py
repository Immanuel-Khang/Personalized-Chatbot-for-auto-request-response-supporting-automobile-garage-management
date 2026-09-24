from typing import Annotated, Literal, Optional, Dict, Any
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

AgentStage = Literal["IDLE", "TU_VAN", "CHO_DUYET", "HOP_DONG", "HO_TRO_BAO_DUONG"]

class HarnessState(TypedDict):
    # Danh sách tin nhắn, tự động nối thêm (append) nhờ add_messages
    messages: Annotated[list[AnyMessage], add_messages]
    
    # State Machine 5 trạng thái
    stage: AgentStage
    
    # Session ID liên kết với Web/Zalo Client
    session_id: str
    
    # Slots dữ liệu trích xuất được
    slots: Dict[str, Any]
    
    # Trị số kiểm soát nghiệp vụ & chống hallucination
    last_verified_price: Optional[float]
    pending_discount_id: Optional[str]