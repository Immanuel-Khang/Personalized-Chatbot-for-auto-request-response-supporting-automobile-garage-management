import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from harness_state import HarnessState
from tools import ALL_TOOLS, get_car_price_tool, search_cars_tool, request_discount_tool, lookup_maintenance_tool
from db_repositories import db

load_dotenv()
# variables for security
api_key = os.getenv("OPEN_AI_KEY")

if api_key is None: 
    raise ValueError("They API key is not found !")

# init AI model
llm = ChatOpenAI(
    model="gpt-5.4-nano",
    temperature=0.2,
    api_key=SecretStr(api_key)
)

# 1. Router Node: Analyse and orchestrate
def router_node(state: HarnessState) -> HarnessState:
    # Nếu đang ở IDLE, tin nhắn đầu tiên tự động chuyển sang TU_VAN
    if state.get("stage") == "IDLE" or not state.get("stage"):
        state["stage"] = "TU_VAN"
    return state

# 2. Node Tư Vấn (TU_VAN)
def tu_van_node(state: HarnessState) -> dict:
    tools_for_sales = [search_cars_tool, get_car_price_tool, request_discount_tool]
    sales_llm = llm.bind_tools(tools_for_sales)
    
    prompt = SystemMessage(
        content=(
            "Bạn là chuyên viên tư vấn bán xe của đại lý ô tô. "
            "Quy tắc tuyệt đối:\n"
            "1. Giá xe BẮT BUỘC phải lấy qua tool get_car_price_tool hoặc search_cars_tool, không bao giờ được tự bịa.\n"
            "2. Khi khách muốn giảm giá, gọi ngay tool request_discount_tool kèm session_id hiện tại: "
            f"'{state['session_id']}'.\n"
            "3. Giọng văn lịch sự, chuyên nghiệp, hỗ trợ tối đa."
        )
    )
    
    messages = [prompt] + state["messages"]
    response = sales_llm.invoke(messages)
    
    return {"messages": [response]}

# 3. Node Chờ Duyệt (CHO_DUYET)
def cho_duyet_node(state: HarnessState) -> dict:
    # Kiểm tra xem Admin đã duyệt chưa qua DB
    req = db.discounts.get_latest_by_session(state["session_id"])
    if req and req.status == "APPROVED":
        state["stage"] = "HOP_DONG"
        msg = SystemMessage(
            content="Yêu cầu giảm giá đã được Quản lý duyệt! Hãy chúc mừng khách và đề nghị ký hợp đồng."
        )
    elif req and req.status == "REJECTED":
        state["stage"] = "TU_VAN"
        msg = SystemMessage(
            content="Rất tiếc yêu cầu giảm giá bị từ chối. Hãy khéo léo thông báo và tư vấn quà tặng phụ kiện khác."
        )
    else: # pending, the state is not yet confirmed
        msg = SystemMessage(
            content=(
                f"Yêu cầu giảm giá (Mã: {state.get('pending_discount_id')}) hiện ĐANG CHỜ QUẢN LÝ PHÊ DUYỆT. "
                "Hãy thông báo khách vui lòng chờ trong giây lát hoặc để lại số điện thoại, nhân viên sẽ liên hệ lại ngay."
            )
        )
    response = llm.invoke([msg] + state["messages"])
    return {"messages": [response], "stage": state["stage"]}

# 4. Node Hợp Đồng (HOP_DONG)
def hop_dong_node(state: HarnessState) -> dict:
    prompt = SystemMessage(
        content=(
            "Khách hàng đã đồng ý mua xe! Nhiệm vụ của bạn là hướng dẫn thủ tục làm hợp đồng: "
            "Xin Họ tên, Số điện thoại, Căn cước công dân và hẹn ngày ký kết/nhận xe."
        )
    )
    response = llm.invoke([prompt] + state["messages"])
    return {"messages": [response]}

# 5. Node Bảo Dưỡng (HO_TRO_BAO_DUONG)
def bao_duong_node(state: HarnessState) -> dict:
    maint_llm = llm.bind_tools([lookup_maintenance_tool])
    prompt = SystemMessage(
        content="Bạn là cố vấn dịch vụ kỹ thuật. Hãy tra cứu lịch và chi phí bảo dưỡng khi khách hỏi số km xe đã chạy."
    )
    response = maint_llm.invoke([prompt] + state["messages"])
    return {"messages": [response]}

# 6. Tool Execution Node
tools_node = ToolNode(ALL_TOOLS)