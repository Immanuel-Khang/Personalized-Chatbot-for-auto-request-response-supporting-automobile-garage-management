from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from harness_state import HarnessState
from agent_nodes import (
    router_node, tu_van_node, cho_duyet_node, 
    hop_dong_node, bao_duong_node, tools_node
)

def route_by_stage(state: HarnessState): # route the node appropriately
    # if no appropriate state is found, default to TU_VAN
    """Router định tuyến dựa trên Stage của State Machine"""
    stage = state.get("stage", "TU_VAN")
    if stage == "TU_VAN":
        return "tu_van_node"
    elif stage == "CHO_DUYET":
        return "cho_duyet_node"
    elif stage == "HOP_DONG":
        return "hop_dong_node"
    elif stage == "HO_TRO_BAO_DUONG":
        return "bao_duong_node"
    return "tu_van_node"

def check_tu_van_tools(state: HarnessState):
    """Kiểm tra xem LLM có muốn gọi Tool hay không"""
    # route to tools node
    last_message = state["messages"][-1]
    if last_message.type == "ai": 
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools_node"
        
    return END

def post_tool_policy_guard(state: HarnessState):
    """
    Sau khi thực thi Tool:
    - Nếu phát hiện POLICY_DISCOUNT_TRIGGERED (> 5% chiết khấu), lập tức chuyển sang CHO_DUYET
    """
    for msg in reversed(state["messages"]):
        if msg.type == "tool":
            if "POLICY_DISCOUNT_TRIGGERED" in msg.content:
                # Trích xuất mã ticket
                import re
                match = re.search(r"DISC-\d+", str(msg.content))
                disc_id = match.group(0) if match else "UNKNOWN"
                return {
                    "stage": "CHO_DUYET",
                    "pending_discount_id": disc_id
                }
    return {"stage": "TU_VAN"}

def build_agent_harness():
    workflow = StateGraph(HarnessState)

    # Thêm các Node
    workflow.add_node("router_node", router_node)
    workflow.add_node("tu_van_node", tu_van_node)
    workflow.add_node("cho_duyet_node", cho_duyet_node)
    workflow.add_node("hop_dong_node", hop_dong_node)
    workflow.add_node("bao_duong_node", bao_duong_node)
    workflow.add_node("tools_node", tools_node)

    # Cấu hình Edges
    workflow.set_entry_point("router_node")
    workflow.add_conditional_edges(
        "router_node",
        route_by_stage,
        {
            "tu_van_node": "tu_van_node",
            "cho_duyet_node": "cho_duyet_node",
            "hop_dong_node": "hop_dong_node",
            "bao_duong_node": "bao_duong_node"
        }
    )

    # Vòng lặp Tools trong Tư vấn
    workflow.add_conditional_edges(
        "tu_van_node",
        check_tu_van_tools,
        {
            "tools_node": "tools_node",
            END: END
        }
    )

    # Sau khi chạy tools xong, kiểm tra policy guard
    def evaluate_after_tool(state: HarnessState):
        decision = post_tool_policy_guard(state)
        if decision.get("stage") == "CHO_DUYET":
            return "cho_duyet_node"
        return "tu_van_node"

    workflow.add_conditional_edges(
        "tools_node",
        evaluate_after_tool,
        {
            "cho_duyet_node": "cho_duyet_node",
            "tu_van_node": "tu_van_node"
        }
    )

    workflow.add_edge("cho_duyet_node", END)
    workflow.add_edge("hop_dong_node", END)
    workflow.add_edge("bao_duong_node", END)

    # Sử dụng MemorySaver Checkpointer để lưu State theo session_id (Thread ID)
    checkpointer = MemorySaver()
    app = workflow.compile(checkpointer=checkpointer)
    return app