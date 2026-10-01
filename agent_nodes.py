# === FILE: agent_nodes.py ===
"""
LangGraph Node Implementations.
Each node is a pure function: (HarnessState) -> dict (partial state update).
"""
import json
import os
from typing import cast, List
from dotenv import load_dotenv
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    SystemMessage, HumanMessage, AIMessage, ToolMessage, AnyMessage
)

from schema import HarnessState, CustomerSlots, IntentAndSlotExtraction, IntentType
from database import db
from tools import (
    ALL_SALES_TOOLS, ALL_MAINTENANCE_TOOLS,
    ALL_APPOINTMENT_TOOLS, ALL_CONTRACT_TOOLS, 
    ALL_FAQ_TOOLS, ALL_LEAD_TOOLS, ALL_TOOLS
)

from datetime import date
from utils import get_next_weekday

load_dotenv()

api_key = os.getenv("OPEN_AI_KEY")

if not api_key: 
    raise ValueError("API key is not found !")

# ─────────────────────────────────────────
# LLM CLIENT
# ─────────────────────────────────────────
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    api_key=SecretStr(api_key), 
)

# ═══════════════════════════════════════════════════════════════
# UTILITY: Message Sanitizer (prevents OpenAI 400 errors)
# ═══════════════════════════════════════════════════════════════

def clean_messages_for_openai(messages: List[AnyMessage]) -> List[AnyMessage]:
    """
    Ensures every AIMessage with tool_calls is strictly followed by matching ToolMessages.
    Synthesizes fallback ToolMessages for any orphaned tool_call_ids to prevent HTTP 400.
    """
    cleaned = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        cleaned.append(msg)

        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            expected_ids = {tc["id"] for tc in msg.tool_calls}
            found_ids = set()
            j = i + 1
            while j < len(messages):
                next_msg = messages[j]
                
                if not isinstance(next_msg, ToolMessage): 
                    break
                
                found_ids.add(next_msg.tool_call_id)
                cleaned.append(next_msg)
                
                j += 1

            for missing_id in expected_ids - found_ids:
                cleaned.append(ToolMessage(
                    content=json.dumps({"error": "Tool execution failed or response was lost."}),
                    tool_call_id=missing_id
                ))
            
            i = j
        else:
            i += 1

    return cleaned


def merge_slots(current: dict, new_slots: dict) -> dict:
    """
    Merge newly extracted slots into the existing slots dict.
    Only overwrites a key if the new value is non-None and non-empty.
    Preserves all previously known information (accumulative memory).
    """
    merged = dict(current or {})
    for key, value in new_slots.items():
        if value is not None and value != "":
            merged[key] = value
    return merged


# ═══════════════════════════════════════════════════════════════
# NODE 1: SLOT FILLER + INTENT CLASSIFIER (Single LLM call)
# ═══════════════════════════════════════════════════════════════

from datetime import date
from typing import cast


def slot_filler_and_intent_node(state: HarnessState) -> dict:
    """
    Dual-purpose preprocessing node:
    1. Classifies customer intent
    2. Extracts structured customer slots
    """

    classifier = llm.with_structured_output(IntentAndSlotExtraction)

    today = date.today()

    system_prompt = (
        "You are a preprocessing agent for a Vietnamese car dealership chatbot.\n\n"

        f"Hôm nay là {today.isoformat()}.\n\n"

        "QUY TẮC XỬ LÝ NGÀY HẸN:\n"
        "- 'ngày 6' nghĩa là ngày 6 của tháng.\n"
        "- 'thứ 6' hoặc 'thứ Sáu' nghĩa là FRIDAY.\n"
        "- 'ngày 6' KHÔNG được hiểu là FRIDAY.\n"
        "- 'thứ 6' KHÔNG được hiểu là ngày 6.\n"
        "- Nếu khách nói một ngày trong tuần, hãy ghi vào "
        "appointment_weekday, KHÔNG ghi vào appointment_date.\n\n"

        "Ví dụ:\n"
        "- 'ngày 6' -> appointment_date = explicit calendar date\n"
        "- 'ngày 6 tháng 10' -> appointment_date = 2026-10-06\n"
        "- 'thứ 6' -> appointment_weekday = FRIDAY\n"
        "- 'thứ Sáu' -> appointment_weekday = FRIDAY\n\n"

        "PRIMARY intent must be one of:\n"
        "SALES, MAINTENANCE, APPOINTMENT, CONTRACT, DISCOUNT, "
        "KNOWLEDGE_FAQ, GENERAL.\n\n"

        "Extract structured data:\n"
        "- car_model\n"
        "- budget_vnd\n"
        "- intended_use\n"
        "- current_mileage_km\n"
        "- appointment_date\n"
        "- appointment_weekday\n"
        "- appointment_time\n"
        "- service_type\n"
        "- customer_name\n"
        "- customer_phone\n"
        "- customer_address\n"
        "- decided_price\n\n"

        "Set fields to null if not mentioned. "
        "Do not guess values."
    )

    latest_human = [
        m for m in state["messages"]
        if isinstance(m, HumanMessage)
    ]

    last_msg = (
        latest_human[-1]
        if latest_human
        else state["messages"][-1]
    )

    result = cast(
        IntentAndSlotExtraction,
        classifier.invoke([
            SystemMessage(content=system_prompt),
            last_msg
        ])
    )

    # ------------------------------------------------
    # Deterministic date normalization
    # ------------------------------------------------

    extracted_slots = result.extracted_slots.model_dump()

    weekday = extracted_slots.get("appointment_weekday")
    appointment_date = extracted_slots.get("appointment_date")

    if weekday and not appointment_date:
        calculated_date = get_next_weekday(
            today,
            weekday
        )

        extracted_slots["appointment_date"] = (
            calculated_date.isoformat()
        )

    # ------------------------------------------------
    # Merge with existing conversation slots
    # ------------------------------------------------

    merged = merge_slots(
        state.get("slots") or {},
        extracted_slots
    )

    return {
        "intent": result.intent.value,
        "slots": merged
    }


# ═══════════════════════════════════════════════════════════════
# NODE 2: BUSINESS ROUTER (Pure logic — no LLM)
# ═══════════════════════════════════════════════════════════════

def business_router_node(state: HarnessState) -> dict:
    """
    State Machine Transition Engine.
    Reads current stage, intent, and DB approval status to decide next stage.
    This node is pure Python logic — zero LLM cost.
    """
    current_stage = state.get("stage") or "IDLE"
    intent = state.get("intent") or ""
    pending_id = state.get("pending_discount_id")

    # ── SCENARIO: Session is locked waiting for discount approval ──
    if current_stage == "CHO_DUYET" and pending_id:
        req = db.discounts.get_by_id(pending_id)
        if req:
            if req.status == "APPROVED":
                return {"stage": "HOP_DONG", "pending_discount_id": None}
            elif req.status == "REJECTED":
                return {"stage": "TU_VAN", "pending_discount_id": None}
            else:
                # Still PENDING — allow bypass for certain intents
                if intent == IntentType.MAINTENANCE.value:
                    return {"stage": "BAO_DUONG"}
                if intent == IntentType.APPOINTMENT.value:
                    return {"stage": "DAT_LICH"}
                if intent == IntentType.KNOWLEDGE_FAQ.value:
                    return {"stage": "KNOWLEDGE"}
                # For DISCOUNT or anything else: let customer re-negotiate
                if intent == IntentType.DISCOUNT.value:
                    return {"stage": "TU_VAN"}
                return {"stage": "CHO_DUYET"}

    # ── SCENARIO: Resolve customer for session continuity ──
    # (We only need to check — actual creation happens in contract_node via tool)
    customer = db.customers.get_by_session(state.get("session_id", ""))
    customer_id_update = {"customer_id": customer.id} if customer else {}

    # ── SCENARIO: Standard intent-to-stage mapping ──
    intent_stage_map = {
        IntentType.SALES.value: "TU_VAN",
        IntentType.DISCOUNT.value: "TU_VAN",
        IntentType.GENERAL.value: "TU_VAN",
        IntentType.MAINTENANCE.value: "BAO_DUONG",
        IntentType.APPOINTMENT.value: "DAT_LICH",
        IntentType.CONTRACT.value: "HOP_DONG",
        IntentType.KNOWLEDGE_FAQ.value: "KNOWLEDGE",
    }

    new_stage = intent_stage_map.get(intent, "TU_VAN")
    return {"stage": new_stage, **customer_id_update}


# ═══════════════════════════════════════════════════════════════
# NODE 3: CONSULTATION NODE (TU_VAN)
# ═══════════════════════════════════════════════════════════════

def consultation_node(state: HarnessState) -> dict:
    """Sales consultation: car info, pricing, discount negotiation."""
    bound_llm = llm.bind_tools(ALL_SALES_TOOLS)
    slots_json = json.dumps(state.get("slots") or {}, ensure_ascii=False)
    session_id = state.get("session_id", "")

    system_prompt = SystemMessage(content=(
        "Bạn là chuyên viên tư vấn bán xe chuyên nghiệp của đại lý ô tô.\n"
        f"THÔNG TIN ĐÃ BIẾT VỀ KHÁCH: {slots_json}\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. Giá xe BẮT BUỘC lấy qua tool 'get_car_price'. KHÔNG BAO GIỜ tự bịa giá.\n"
        "2. Khi khách yêu cầu giảm giá, gọi tool 'request_discount' với tên xe và % giảm.\n"
        "3. Tư vấn thân thiện, xưng 'em', gọi khách là 'anh/chị'.\n"
        "4. Không hỏi lại những thông tin đã có trong THÔNG TIN ĐÃ BIẾT."
    ))

    safe_messages = clean_messages_for_openai(state["messages"])
    response = bound_llm.invoke([system_prompt] + safe_messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 4: MAINTENANCE NODE (BAO_DUONG)
# ═══════════════════════════════════════════════════════════════

def maintenance_node(state: HarnessState) -> dict:
    """Service advisor: lookup maintenance schedule, advise on service packages."""
    bound_llm = llm.bind_tools(ALL_MAINTENANCE_TOOLS)
    slots_json = json.dumps(state.get("slots") or {}, ensure_ascii=False)

    system_prompt = SystemMessage(content=(
        "Bạn là cố vấn dịch vụ kỹ thuật của xưởng ô tô.\n"
        f"THÔNG TIN ĐÃ BIẾT: {slots_json}\n\n"
        "QUY TẮC:\n"
        "1. Nếu đã có 'car_model' VÀ 'current_mileage_km' trong thông tin đã biết, "
        "GỌI NGAY tool 'lookup_maintenance_schedule'. KHÔNG hỏi lại.\n"
        "2. Nếu chưa có car_model: hỏi tên xe.\n"
        "3. Nếu chưa có mileage: hỏi số km đã chạy.\n"
        "4. Sau khi có kết quả từ tool, gợi ý khách đặt lịch vào xưởng."
    ))

    safe_messages = clean_messages_for_openai(state["messages"])
    response = bound_llm.invoke([system_prompt] + safe_messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 5: APPOINTMENT NODE (DAT_LICH)
# ═══════════════════════════════════════════════════════════════

def appointment_node(state: HarnessState) -> dict:
    """Appointment scheduler: collect date/time/service type and book a slot."""
    bound_llm = llm.bind_tools(ALL_APPOINTMENT_TOOLS)
    slots = state.get("slots") or {}
    slots_json = json.dumps(slots, ensure_ascii=False)

    session_id = state["session_id"]
    system_prompt = SystemMessage(content=(
        "Bạn là nhân viên điều phối lịch dịch vụ của xưởng ô tô.\n"
        f"THÔNG TIN ĐÃ BIẾT: {slots_json}\n\n"
        f"SESSION ID HIỆN TẠI: {session_id}\n"

        "ĐỂ ĐẶT LỊCH CẦN ĐỦ: tên khách, SĐT, dòng xe, ngày hẹn, giờ hẹn, loại dịch vụ.\n"
        """QUY TẮC:\n
        1. Kiểm tra thông tin đã biết. Chỉ hỏi những thông tin CÒN THIẾU.\n"
        2. Khi ĐỦ tất cả thông tin, BẮT BUỘC gọi tool 'schedule_appointment'.
        3. TUYỆT ĐỐI KHÔNG tự xác nhận đặt lịch nếu chưa nhận được kết quả từ tool.
        4. Chỉ được nói "đã xác nhận" khi tool trả về status = "CONFIRMED".
        5. Nếu tool trả về "PENDING_ADMIN", phải thông báo khách đang chờ xác nhận.
        6. Không được tự tạo appointment_id. Chỉ sử dụng appointment_id do tool trả về.
        """
    ))

    print("\n===== APPOINTMENT SLOTS =====")
    print(json.dumps(slots, ensure_ascii=False, indent=2))
    print("=============================\n")
    
    safe_messages = clean_messages_for_openai(state["messages"])
    response = bound_llm.invoke([system_prompt] + safe_messages)
    
    print("\n===== LLM RESPONSE =====")
    print("Content:", response.content)
    print("Tool calls:", response.tool_calls)
    print("========================\n")

    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 6: CONTRACT NODE (HOP_DONG)
# ═══════════════════════════════════════════════════════════════

def contract_node(state: HarnessState) -> dict:
    """Contract closing: collect full KYC, save customer profile, finalize sale."""
    bound_llm = llm.bind_tools(ALL_CONTRACT_TOOLS)
    slots = state.get("slots") or {}
    session_id = state.get("session_id", "")

    required_fields = {
        "customer_name": "Họ và tên",
        "customer_phone": "Số điện thoại",
        "customer_address": "Địa chỉ nhận xe",
        "car_model": "Dòng xe chốt mua",
        "decided_price": "Giá chốt hợp đồng (VNĐ)"
    }
    missing = [label for key, label in required_fields.items() if not slots.get(key)]
    slots_json = json.dumps(slots, ensure_ascii=False)

    system_prompt = SystemMessage(content=(
        "Bạn là chuyên viên làm thủ tục hợp đồng mua bán xe.\n"
        f"THÔNG TIN ĐÃ CÓ: {slots_json}\n"
        f"THÔNG TIN CÒN THIẾU: {missing}\n"
        f"SESSION_ID: {session_id}\n\n"
        "QUY TẮC:\n"
        "1. Nếu còn thông tin thiếu, xin lịch sự từng thông tin một.\n"
        "2. Khi ĐỦ TẤT CẢ thông tin: gọi tool 'save_customer_profile' với session_id="
        f"'{session_id}', name, phone, address.\n"
        "3. Sau khi lưu thành công: tóm tắt hợp đồng và hẹn ngày ký kết/giao xe."
    ))

    safe_messages = clean_messages_for_openai(state["messages"])
    response = bound_llm.invoke([system_prompt] + safe_messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 7: PENDING DISCOUNT NODE (CHO_DUYET)
# ═══════════════════════════════════════════════════════════════

def pending_discount_node(state: HarnessState) -> dict:
    """Inform customer that their discount request is under review."""
    pending_id = state.get("pending_discount_id", "N/A")
    req = db.discounts.get_by_id(pending_id) if pending_id else None
    car_name = req.car_name if req else "xe"
    pct = req.discount_percent if req else "?"

    system_prompt = SystemMessage(content=(
        f"Yêu cầu giảm giá {pct}% cho {car_name} (Mã: {pending_id}) đang chờ Quản lý phê duyệt. "
        "Hãy thông báo lịch sự cho khách biết trạng thái này, đề nghị khách kiên nhẫn chờ, "
        "và hỏi có cần hỗ trợ thêm gì khác không (như xem dòng xe khác, hỏi bảo dưỡng...)."
    ))

    response = llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 8: KNOWLEDGE / FAQ NODE (KNOWLEDGE)
# ═══════════════════════════════════════════════════════════════

def knowledge_faq_node(state: HarnessState) -> dict:
    """
    Action-Aware Knowledge Node:
    1. Calls query_foton_faq tool to search vector store
    2. If action_type == COLLECT_LEAD/TEST_DRIVE: switches to lead collection mode
    3. If OUT_OF_FAQ: answers with disclaimer or escalates to human
    """
    
    bound_llm = llm.bind_tools(ALL_FAQ_TOOLS + ALL_LEAD_TOOLS)
    slots_json = json.dumps(state.get("slots") or {}, ensure_ascii=False)
    system_prompt = SystemMessage(content=(
        "Bạn là chuyên viên tư vấn thông tin Foton Việt Nam.\n"
        f"THÔNG TIN ĐÃ BIẾT: {slots_json}\n\n"
        "QUY TRÌNH XỬ LÝ:\n"
        "1. Luôn gọi tool 'query_foton_faq' với câu hỏi của khách để tìm câu trả lời chuẩn.\n"
        "2. Nếu kết quả trả về 'EXACT_MATCH': Dùng trường 'answer' làm câu trả lời.\n"
        "3. Nếu 'action_type' là 'COLLECT_LEAD' hoặc 'TEST_DRIVE_BOOKING':\n"
        "   - Trả lời câu hỏi trước, SAU ĐÓ xin thông tin theo 'required_slots'.\n"
        "   - Khi đủ thông tin: gọi tool 'create_lead'.\n"
        "4. Nếu 'PARTIAL_MATCH': Hỏi lại khách để xác nhận.\n"
        "5. Nếu 'OUT_OF_FAQ': Trả lời theo kiến thức phổ thông với disclaimer bắt buộc.\n"
        "6. KHÔNG BAO GIỜ bịa đặt chính sách bảo hành, thời gian, hoặc số liệu cụ thể."
    ))
    safe_messages = clean_messages_for_openai(state["messages"])
    response = bound_llm.invoke([system_prompt] + safe_messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# NODE 9: POLICY GUARD NODE
# ═══════════════════════════════════════════════════════════════

def policy_guard_node(state: HarnessState) -> dict:
    """
    Inspects ONLY the latest tool message from this turn.
    - If requires_manager_approval == True (discount > 5%): -> CHO_DUYET
    - If status == AUTO_APPROVED (discount <= 5%): clear pending, -> TU_VAN
    - Otherwise: -> TU_VAN (pass-through)
    """
    # Only inspect the very last message — never scan full history
    latest = state["messages"][-1] if state["messages"] else None

    if latest is None or latest.type != "tool":
        return {"stage": "TU_VAN"}

    try:
        payload = json.loads(str(latest.content))
        if not isinstance(payload, dict):
            return {"stage": "TU_VAN"}

        if payload.get("requires_manager_approval") is True:
            req_id = payload.get("request_id", "UNKNOWN")
            # Patch the session_id on the discount record
            session_id = state.get("session_id", "")
            req = db.discounts.get_by_id(req_id)
            if req:
                db.discounts.update(req_id, session_id=session_id)
            print(f"🚨 [POLICY GUARD] Discount > 5% detected. Ticket: {req_id}")
            return {"stage": "CHO_DUYET", "pending_discount_id": req_id}

        if payload.get("status") == "AUTO_APPROVED":
            print(f"✅ [POLICY GUARD] Discount <= 5% auto-approved.")
            return {"stage": "TU_VAN", "pending_discount_id": None}

    except (json.JSONDecodeError, TypeError):
        pass

    return {"stage": "TU_VAN"}
