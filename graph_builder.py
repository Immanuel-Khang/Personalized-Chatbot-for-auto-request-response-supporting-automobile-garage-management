from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage
from schema import HarnessState
from tools import ALL_TOOLS
from agent_nodes import (
    intent_classifier_node,
    business_router_node,
    consultation_node,
    maintenance_node,
    contract_node,
    pending_approval_node,
    policy_guard_node,
)

def route_business_stage(state: HarnessState) -> str:
    stage = state.get("stage", "TU_VAN")
    if stage == "BAO_DUONG":
        return "maintenance_node"
    elif stage == "HOP_DONG":
        return "contract_node"
    elif stage == "CHO_DUYET":
        return "pending_approval_node"
    return "consultation_node"

def check_consultation_tool_calls(state: HarnessState) -> str:
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage):
        if last_msg.tool_calls:
            return "tools_node"
    return END

def evaluate_policy_guard_branch(state: HarnessState) -> str:
    if state.get("stage") == "CHO_DUYET":
        return "pending_approval_node"
    return "consultation_node"

def route_after_tools(state: HarnessState) -> str:
    stage = state.get("stage")
    if stage == "BAO_DUONG":
        return "maintenance_node"   # Quay lại để model đọc kết quả tool và trả lời khách
    return "policy_guard_node"     # Nếu là TU_VAN thì đi qua kiểm tra chiết khấu

# 1. Hàm kiểm tra maintenance_node có gọi tool không
def check_maintenance_tool_calls(state: HarnessState) -> str:
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage): 
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools_node"
    return END


def build_graph():
    workflow = StateGraph(HarnessState)

    # 1. Register Nodes
    workflow.add_node("intent_classifier_node", intent_classifier_node)
    workflow.add_node("business_router_node", business_router_node)
    workflow.add_node("consultation_node", consultation_node)
    workflow.add_node("maintenance_node", maintenance_node)
    workflow.add_node("contract_node", contract_node)
    workflow.add_node("pending_approval_node", pending_approval_node)
    workflow.add_node("tools_node", ToolNode(ALL_TOOLS))
    workflow.add_node("policy_guard_node", policy_guard_node)

    # 2. Graph Wiring
    workflow.set_entry_point("intent_classifier_node")
    workflow.add_edge("intent_classifier_node", "business_router_node")

    # Conditional router edge
    workflow.add_conditional_edges(
        "business_router_node",
        route_business_stage,
        {
            "consultation_node": "consultation_node",
            "maintenance_node": "maintenance_node",
            "contract_node": "contract_node",
            "pending_approval_node": "pending_approval_node",
        }
    )

    # Consultation tool loop
    workflow.add_conditional_edges(
        "consultation_node",
        check_consultation_tool_calls,
        {
            "tools_node": "tools_node",
            END: END
        }
    )
    
    workflow.add_conditional_edges(
        "maintenance_node",
        check_maintenance_tool_calls,
        {
            "tools_node": "tools_node",
            END: END
        }
    )

    # Tools execution flows directly into Policy Guard
    workflow.add_edge("tools_node", "policy_guard_node")

    # Policy Guard branches to either TU_VAN (continue loop) or CHO_DUYET
    workflow.add_conditional_edges(
        "policy_guard_node",
        evaluate_policy_guard_branch,
        {
            "consultation_node": "consultation_node",
            "pending_approval_node": "pending_approval_node"
        }
    )

    workflow.add_edge("maintenance_node", END)
    workflow.add_edge("contract_node", END)
    workflow.add_edge("pending_approval_node", END)

    # State checkpointing
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)