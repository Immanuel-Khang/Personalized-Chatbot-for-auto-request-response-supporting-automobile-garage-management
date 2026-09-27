import os
import json

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from schema import HarnessState, IntentAndSlotExtraction, IntentType, CustomerSlots
from typing import cast 
from pydantic import SecretStr
from database import db_container
from tools import search_cars, get_car_price, request_discount, lookup_maintenance_schedule

load_dotenv()
# API key for security
api_key = os.getenv("OPEN_AI_KEY")

if api_key is None: 
    raise ValueError("API key not found !")

llm = ChatOpenAI(
    model="gpt-5.4-nano",
    temperature=0.2,
    api_key=SecretStr(api_key)
)

# 1. Intent Classifier Node
def intent_classifier_node(state: HarnessState) -> dict:
    # LLM vừa phân loại Intent, vừa nhặt thông tin điền vào Slots
    extractor = llm.with_structured_output(IntentAndSlotExtraction)
    
    system_prompt = (
        "Classify the customer's latest request into exactly one intent category:\n"
        "- SALES: Inquiring about car features, models, catalog, or prices.\n"
        "- MAINTENANCE: Inquiring about service milestones, repair, maintenance cost.\n"
        "- CONTRACT: Ready to purchase, deposit, sign agreement, provide KYC.\n"
        "- DISCOUNT: Asking for special deals, negotiations, price cuts.\n"
        "- GENERAL: Greetings, thanks, or general inquiry."
        "Phân loại Intent và trích xuất mọi thông tin chi tiết (nếu có) từ tin nhắn của khách: "
        "Dòng xe, ngân sách, số km, tên, số điện thoại..."
    )
    
    latest_user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = latest_user_messages[-1] if latest_user_messages else state["messages"][-1]
    
    result = cast(
        IntentAndSlotExtraction, 
        extractor.invoke([system_prompt] + [last_msg])
    )
    # 1. LẤY SLOTS CŨ TỪ STATE (KHÔNG ĐƯỢC XÓA)
    raw_slots = state.get("slots")
    if isinstance(raw_slots, CustomerSlots):
        merged_slots = raw_slots.model_dump()
    elif isinstance(raw_slots, dict):
        merged_slots = raw_slots.copy()
    else:
        merged_slots = CustomerSlots().model_dump()
    # 2. CHỈ CẬP NHẬT TRƯỜNG NÀO CÓ DỮ LIỆU MỚI (Tránh ghi đè None lên giá trị cũ)
    if result.extracted_slots:
        new_data = result.extracted_slots.model_dump(exclude_unset=True)
        for key, value in new_data.items():
            if value is not None and value != "":
                merged_slots[key] = value  # Giữ lại car_model cũ, thêm mileage_km mới!
    return {
        "intent": result.intent.value,
        "slots": CustomerSlots(**merged_slots)
    }

# 2. Business Router Node
def business_router_node(state: HarnessState) -> dict:
    current_stage = state.get("stage") or "IDLE"
    intent = state.get("intent")
    pending_discount_id = state.get("pending_discount_id")

    # --- Scenario A: Session is locked in CHO_DUYET ---
    if current_stage == "CHO_DUYET" and pending_discount_id:
        discount_req = db_container.discounts.get_by_id(pending_discount_id)
        if discount_req:
            if discount_req.status == "APPROVED":
                return {"stage": "HOP_DONG", "pending_discount_id": None}
            elif discount_req.status == "REJECTED":
                return {"stage": "TU_VAN", "pending_discount_id": None}
            else:
                return {"stage": "CHO_DUYET"}

    # --- Scenario B: Standard Intent-driven Transition ---
    if intent == IntentType.MAINTENANCE.value:
        return {"stage": "BAO_DUONG"}
    elif intent == IntentType.CONTRACT.value:
        return {"stage": "HOP_DONG"}
    elif intent in [IntentType.SALES.value, IntentType.DISCOUNT.value, IntentType.GENERAL.value]:
        return {"stage": "TU_VAN"}
    
    return {"stage": "TU_VAN"}

# 3. Consultation Node (TU_VAN)
def consultation_node(state: HarnessState) -> dict:
    consultation_tools = [search_cars, get_car_price, request_discount]
    bound_llm = llm.bind_tools(consultation_tools)
    
    system_prompt = SystemMessage(
        content=(
            "You are a car dealership sales assistant. "
            "Always fetch official car prices using 'get_car_price'. Never guess prices. "
            f"""
            Current session_id is '{state['session_id']}'. Pass it when calling 'request_discount'.
            If the discount is AUTO_APPROVED, confirm that the discount has been applied, 
            calculate the updated price and resend it 
            """
            f"The customer's related information: {state['slots'].model_dump_json()}\n"
            f"Rule: Use the above information for personalized consultation, "
            f"Do not reask the already provided information."
        )
    )
    
    response = bound_llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 4. Maintenance Node (BAO_DUONG)
def maintenance_node(state: HarnessState) -> dict:
    bound_llm = llm.bind_tools([lookup_maintenance_schedule])
    system_prompt = SystemMessage(
        content=(
        "You are a car service advisor." 
        "Assist customers with maintenance schedules, tasks, and costs."
        )
    )
    response = bound_llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 5. Contract Node (HOP_DONG)
def contract_node(state: HarnessState) -> dict:
    system_prompt = SystemMessage(
        content=(
            "The customer is finalizing their purchase! "
            "Congratulate them and collect their legal details (Full Name, Phone, National ID) to prepare the contract."
        )
    )
    response = llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 6. Pending Approval Node (CHO_DUYET)
def pending_approval_node(state: HarnessState) -> dict:
    req_id = state.get("pending_discount_id", "CURRENT_REQUEST")
    system_prompt = SystemMessage(
        content=(
            f"The customer's discount request (Ticket: {req_id}) is currently pending managerial review. "
            "Politely advise them to wait for approval and ask if they have any other questions."
        )
    )
    response = llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 7. Policy Guard Node

def policy_guard_node(state: HarnessState) -> dict:
    """
    Kiểm tra kết quả thực thi của các Tool gần nhất:
    - Nếu phát hiện yêu cầu giảm giá vượt thẩm quyền (> 5%), chuyển stage -> 'CHO_DUYET'.
    - Ngược lại, tiếp tục ở stage -> 'TU_VAN'.
    """
    # 1. Lọc lấy các tool messages theo thứ tự TỪ MỚI ĐẾN CŨ
    recent_tool_messages = [m for m in reversed(state["messages"]) if m.type == "tool"]
    
    if not recent_tool_messages:
        return {"stage": "TU_VAN"}
    # 2. Quét qua các tool vừa chạy trong lượt này
    for tool_msg in recent_tool_messages:
        content = tool_msg.content
        if not content:
            continue
            
        try:
            # Parse JSON an toàn
            payload = json.loads(str(content))
            
            # Kiểm tra cờ duyệt chiết khấu
            if isinstance(payload, dict) and payload.get("requires_manager_approval") is True:
                print(f"🚨 [POLICY GUARD]: Kích hoạt duyệt cho Ticket {payload.get('request_id')}")
                return {
                    "stage": "CHO_DUYET",
                    "pending_discount_id": payload.get("request_id")
                }
        except (json.JSONDecodeError, TypeError):
            # Nếu tool trả về chuỗi text thường (không phải JSON), bỏ qua êm đẹp, KHÔNG ĐƯỢC CRASH!
            continue
    # 3. Mặc định: Nếu không có vi phạm policy nào, tiếp tục ở TU_VAN
    return {"stage": "TU_VAN"}