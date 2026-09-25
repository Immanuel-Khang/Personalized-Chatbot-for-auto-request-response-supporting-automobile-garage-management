import os
import json

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from schema import HarnessState, IntentClassificationResult, IntentType
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
    classifier = llm.with_structured_output(IntentClassificationResult)
    
    system_prompt = (
        "Classify the customer's latest request into exactly one intent category:\n"
        "- SALES: Inquiring about car features, models, catalog, or prices.\n"
        "- MAINTENANCE: Inquiring about service milestones, repair, maintenance cost.\n"
        "- CONTRACT: Ready to purchase, deposit, sign agreement, provide KYC.\n"
        "- DISCOUNT: Asking for special deals, negotiations, price cuts.\n"
        "- GENERAL: Greetings, thanks, or general inquiry."
    )
    
    # Optimize: send only system prompt and the last customer message
    latest_user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = latest_user_messages[-1] if latest_user_messages else state["messages"][-1]
    
    result = cast(
        IntentClassificationResult,
        classifier.invoke([
            SystemMessage(content=system_prompt),
            last_msg
        ])
    )
    return {"intent": result.intent.value}

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
            f"Current session_id is '{state['session_id']}'. Pass it when calling 'request_discount'."
        )
    )
    response = bound_llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 4. Maintenance Node (BAO_DUONG)
def maintenance_node(state: HarnessState) -> dict:
    bound_llm = llm.bind_tools([lookup_maintenance_schedule])
    system_prompt = SystemMessage(
        content="You are a car service advisor. Assist customers with maintenance schedules, tasks, and costs."
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
    Inspects tool execution outputs.
    If 'requires_manager_approval' == True (Discount > 5%), transition stage to 'CHO_DUYET'.
    """
    latest_tool_messages = [m for m in reversed(state["messages"]) if m.type == "tool"]
    
    for tool_msg in latest_tool_messages:
        try:
            payload = json.loads(str(tool_msg.content))
            if isinstance(payload, dict) and payload.get("requires_manager_approval") is True:
                return {
                    "stage": "CHO_DUYET",
                    "pending_discount_id": payload.get("request_id")
                }
        except (json.JSONDecodeError, TypeError):
            continue

    return {"stage": "TU_VAN"}