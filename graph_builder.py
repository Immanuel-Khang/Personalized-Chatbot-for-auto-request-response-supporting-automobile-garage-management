"""
LangGraph StateGraph Assembly.
Wires all nodes, routing functions, and the shared ToolNode into a compiled graph.
"""
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage

from schema import HarnessState
from tools import ALL_TOOLS
from agent_nodes import (
    slot_filler_and_intent_node,
    business_router_node,
    consultation_node,
    maintenance_node,
    appointment_node,
    contract_node,
    pending_discount_node,
    knowledge_faq_node,
    policy_guard_node,
)

# ═══════════════════════════════════════════════════════════════
# ROUTING FUNCTIONS (Conditional Edge Logic)
# ═══════════════════════════════════════════════════════════════

def route_by_stage(state: HarnessState) -> str:
    """
    Read state['stage'] (set by business_router_node) and return the target node name.
    business_router_node runs first and always updates stage — so this is a clean read.
    """
    stage = state.get("stage", "TU_VAN")
    routing_map = {
        "TU_VAN": "consultation_node",
        "BAO_DUONG": "maintenance_node",
        "DAT_LICH": "appointment_node",
        "HOP_DONG": "contract_node",
        "CHO_DUYET": "pending_discount_node",
        "KNOWLEDGE": "knowledge_faq_node",
        "IDLE": "consultation_node",
    }
    return routing_map.get(stage, "consultation_node")


def check_tool_calls(state: HarnessState) -> str:
    """
    After a working node (consultation, maintenance, appointment, contract) runs,
    check if the LLM requested any tool calls.
    If yes: send to tools_node.
    If no: the node already has its final response — end the turn.
    """
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
        return "tools_node"
    return END


def route_after_tools(state: HarnessState) -> str:
    """
    After tools_node executes, decide where to route based on:
    - The current stage (so maintenance loops back to maintenance_node, not consultation)
    - For TU_VAN/DISCOUNT: route through policy_guard_node first
    """
    stage = state.get("stage", "TU_VAN")

    if stage == "BAO_DUONG":
        return "maintenance_node"       # Let maintenance_node read tool result and reply
    if stage == "DAT_LICH":
        return "appointment_node"       # Appointment reads confirmation and replies
    if stage == "HOP_DONG":
        return "contract_node"          # Contract reads save result and proceeds
    # Default: TU_VAN / CHO_DUYET / anything sales-related must pass through Policy Guard
    return "policy_guard_node"


def evaluate_policy_guard(state: HarnessState) -> str:
    """
    After policy_guard_node updates state['stage'], route accordingly.
    """
    stage = state.get("stage", "TU_VAN")
    if stage == "CHO_DUYET":
        return "pending_discount_node"
    return "consultation_node"


# ═══════════════════════════════════════════════════════════════
# GRAPH ASSEMBLY
# ═══════════════════════════════════════════════════════════════

def build_graph():
    """
    Assembles and compiles the full LangGraph StateGraph for the Foton chatbot harness.
    Returns a compiled app ready for app.invoke() or app.stream().
    """
    workflow = StateGraph(HarnessState)

    # ── Register all nodes ──
    workflow.add_node("slot_filler_and_intent_node", slot_filler_and_intent_node)
    workflow.add_node("business_router_node", business_router_node)
    workflow.add_node("consultation_node", consultation_node)
    workflow.add_node("maintenance_node", maintenance_node)
    workflow.add_node("appointment_node", appointment_node)
    workflow.add_node("contract_node", contract_node)
    workflow.add_node("pending_discount_node", pending_discount_node)
    workflow.add_node("knowledge_faq_node", knowledge_faq_node)
    workflow.add_node("policy_guard_node", policy_guard_node)
    workflow.add_node("tools_node", ToolNode(ALL_TOOLS))

    # ── Entry point ──
    workflow.set_entry_point("slot_filler_and_intent_node")

    # ── Fixed edges ──
    workflow.add_edge("slot_filler_and_intent_node", "business_router_node")

    # ── Business router -> working nodes (conditional by stage) ──
    workflow.add_conditional_edges(
        "business_router_node",
        route_by_stage,
        {
            "consultation_node": "consultation_node",
            "maintenance_node": "maintenance_node",
            "appointment_node": "appointment_node",
            "contract_node": "contract_node",
            "pending_discount_node": "pending_discount_node",
            "knowledge_faq_node": "knowledge_faq_node",
        }
    )

    # ── Working nodes -> either call tools or end turn ──
    for node_name in ["consultation_node", "maintenance_node", "appointment_node", "contract_node", "knowledge_faq_node"]:
        workflow.add_conditional_edges(
            node_name,
            check_tool_calls,
            {
                "tools_node": "tools_node",
                END: END,
            }
        )

    # ── Terminal nodes go straight to END ──
    workflow.add_edge("pending_discount_node", END)
    
    # ── After tools execute: route to correct node ──
    workflow.add_conditional_edges(
        "tools_node",
        route_after_tools,
        {
            "maintenance_node": "maintenance_node",
            "appointment_node": "appointment_node",
            "contract_node": "contract_node",
            "policy_guard_node": "policy_guard_node",
        }
    )

    # ── Policy guard branching ──
    workflow.add_conditional_edges(
        "policy_guard_node",
        evaluate_policy_guard,
        {
            "consultation_node": "consultation_node",
            "pending_discount_node": "pending_discount_node",
        }
    )

    # ── Compile with in-memory checkpointing (replace with PostgresSaver for production) ──
    checkpointer = MemorySaver()
    app = workflow.compile(checkpointer=checkpointer)
    return app
