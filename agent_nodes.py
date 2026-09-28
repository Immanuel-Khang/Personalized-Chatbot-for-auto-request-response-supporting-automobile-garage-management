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

# 1. Intent Classifier and Slot Filling Node
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
        """
        Phân loại Intent và trích xuất mọi thông tin chi tiết (nếu có) từ tin nhắn của khách: 
        - car_model: Tên dòng xe\n
        - budget_vnd: Ngân sách dự kiến\n
        - intended_use: Mục đích mua xe\n
        - current_mileage_km: Số km bảo dưỡng\n
        - customer_name: Họ tên khách hàng\n
        - customer_phone: Số điện thoại\n
        - customer_address: Địa chỉ, quận/huyện, tỉnh thành nơi khách ở\n
        - decided_price: Mức giá khách hàng đồng ý chốt mua (VNĐ)
        """
    )
    
    latest_user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = latest_user_messages[-1] if latest_user_messages else state["messages"][-1]
    
    result = cast(
        IntentAndSlotExtraction, 
        extractor.invoke([system_prompt] + [last_msg])
    )
    # 1. LẤY SLOTS CŨ TỪ STATE (KHÔNG ĐƯỢC XÓA)
    merged_slots = dict(state.get("slots") or {})

    # 2. CHỈ CẬP NHẬT TRƯỜNG NÀO CÓ DỮ LIỆU MỚI (Tránh ghi đè None lên giá trị cũ)
    if result.extracted_slots:
        new_data = result.extracted_slots.model_dump(exclude_unset=True)
        for key, value in new_data.items():
            if value is not None and value != "":
                merged_slots[key] = value  # Giữ lại car_model cũ, thêm mileage_km mới!
    return {
        "intent": result.intent.value,
        "slots": merged_slots
    }

# 2. Business Router Node
def business_router_node(state: HarnessState) -> dict:
    current_stage = state.get("stage") or "IDLE"
    intent = state.get("intent")
    pending_discount_id = state.get("pending_discount_id")

    if current_stage == "CHO_DUYET" and pending_discount_id:
        discount_req = db_container.discounts.get_by_id(pending_discount_id)
        if discount_req:
            if discount_req.status == "APPROVED":
                return {"stage": "HOP_DONG", "pending_discount_id": None}
            elif discount_req.status == "REJECTED":
                return {"stage": "TU_VAN", "pending_discount_id": None}
            else:
                # Nếu khách muốn đàm phán con số khác -> Cho qua TU_VAN để chạy lại tool!
                if intent == "DISCOUNT":
                    return {"stage": "TU_VAN"}
                if intent == "MAINTENANCE":
                    return {"stage": "BAO_DUONG"}
                # Chỉ khi nào khách hỏi vu vơ/chờ đợi mới giữ ở CHO_DUYET
                return {"stage": "CHO_DUYET"}

    # Các trường hợp thông thường
    if intent == "MAINTENANCE":
        return {"stage": "BAO_DUONG"}
    elif intent == "CONTRACT":
        return {"stage": "HOP_DONG"}
    else:
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
            f"The customer's related information: {state['slots'].copy()}\n"
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

# 5 Contract Node (HOP_DONG)
def contract_node(state: HarnessState) -> dict:
    slots = state.get("slots") or {}
    
    # Liệt kê các thông tin cần thiết làm hợp đồng
    required_fields = {
        "customer_name": "Họ và tên",
        "customer_phone": "Số điện thoại",
        "customer_address": "Địa chỉ nhận xe / hộ khẩu",
        "car_model": "Dòng xe chọn mua",
        "decided_price": "Mức giá chốt hợp đồng"
    }
    
    # Tìm xem còn thiếu trường nào
    missing_fields = [label for key, label in required_fields.items() if not slots.get(key)]
    
    system_prompt = SystemMessage(
        content=(
            "Bạn là chuyên viên pháp lý và thủ tục hợp đồng mua xe.\n"
            f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(slots, ensure_ascii=False)}\n\n"
            f"⚠️ CÁC THÔNG TIN CÒN THIẾU CẦN THU THẬP: {missing_fields}\n\n"
            "QUY TẮC:\n"
            "1. Nếu còn thông tin thiếu, hãy chúc mừng khách đã chốt xe và nhẹ nhàng xin nốt các thông tin còn thiếu trên.\n"
            "2. Nếu đã ĐỦ TẤT CẢ thông tin, hãy tóm tắt lại toàn bộ hợp đồng (Tên, SĐT, Địa chỉ, Dòng xe, Giá chốt) "
            "và hẹn ngày ký kết/bàn giao xe."
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
    Chỉ kiểm tra Tool vừa thực thi ở lượt chat hiện tại (tin nhắn cuối cùng trong messages).
    """
    latest_msg = state["messages"][-1]
    
    # 1. Nếu tin nhắn vừa rồi là ToolMessage
    if latest_msg.type == "tool":
        try:
            payload = json.loads(str(latest_msg.content))
            
            # Nếu lượt này khách xin mức mới > 5% -> Sang CHO_DUYET
            if isinstance(payload, dict) and payload.get("requires_manager_approval") is True:
                print(f"🚨 [POLICY GUARD]: Kích hoạt duyệt cho Ticket {payload.get('request_id')}")
                return {
                    "stage": "CHO_DUYET",
                    "pending_discount_id": payload.get("request_id")
                }
            
            # Nếu lượt này khách xin mức <= 5% (Đã được duyệt tự động) -> XÓA pending_discount_id cũ!
            elif isinstance(payload, dict) and payload.get("status") == "AUTO_APPROVED":
                print(f"✅ [POLICY GUARD]: Mức giảm {payload.get('discount_percent')}% được tự động duyệt.")
                return {
                    "stage": "TU_VAN",
                    "pending_discount_id": None  # 🟢 HỦY BỎ TICKET CHỜ DUYỆT CŨ
                }
        except (json.JSONDecodeError, TypeError):
            pass

    return {"stage": "TU_VAN"}