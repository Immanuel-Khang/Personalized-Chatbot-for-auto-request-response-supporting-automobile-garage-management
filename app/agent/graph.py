"""
[TRACK B] Lắp ráp LangGraph. Sơ đồ luồng:
preprocess -> policy_guard -> {human_handover | knowledge | router}
router -> {sales | contract | appointment | knowledge | respond}
sales/contract/knowledge -> {human_handover (needs_human) | appointment (handoff) | output_guard}
appointment -> output_guard -> {respond | human_handover};  human_handover -> respond -> END
Thêm node mới: viết file trong nodes/, add_node + add_edge ở đây. Không sửa chỗ khác.
"""
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.appointment import appointment
from app.agent.nodes.contract import contract
from app.agent.nodes.human_handover import human_handover
from app.agent.nodes.knowledge import knowledge
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.policy_guard import policy_guard
from app.agent.nodes.preprocess import preprocess
from app.agent.nodes.respond import respond
from app.agent.nodes.router import pick_agent, router
from app.agent.nodes.sales import sales
from app.agent.state import AgentState


def after_agent(state: AgentState) -> str:
    """Agent tự yêu cầu chuyển người -> human_handover; muốn đặt lịch -> appointment; còn lại -> output_guard."""
    if state.get("needs_human"):
        return "human_handover"
    if state.get("handoff_to") == "appointment":
        return "appointment"
    return "output_guard"


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [
        ("preprocess", preprocess), ("policy_guard", policy_guard), ("router", router),
        ("sales", sales), ("contract", contract), ("appointment", appointment),
        ("knowledge", knowledge), ("output_guard", output_guard),
        ("human_handover", human_handover), ("respond", respond),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "policy_guard")
    g.add_conditional_edges(
        "policy_guard", lambda s: s["route"],
        {"human": "human_handover", "knowledge": "knowledge", "router": "router"},
    )
    g.add_conditional_edges(
        "router", pick_agent,
        {"sales": "sales", "contract": "contract", "appointment": "appointment",
         "knowledge": "knowledge", "respond": "respond"},
    )
    g.add_conditional_edges(
        "sales", after_agent,
        {"human_handover": "human_handover", "appointment": "appointment", "output_guard": "output_guard"},
    )
    g.add_conditional_edges(
        "knowledge", after_agent,
        {"human_handover": "human_handover", "appointment": "appointment", "output_guard": "output_guard"},
    )
    g.add_conditional_edges(
        "contract", after_agent,
        {"human_handover": "human_handover", "output_guard": "output_guard"},
    )
    g.add_edge("appointment", "output_guard")
    g.add_conditional_edges(
        "output_guard", lambda s: "human_handover" if s.get("needs_human") else "respond",
        {"human_handover": "human_handover", "respond": "respond"},
    )
    g.add_edge("human_handover", "respond")
    g.add_edge("respond", END)
    # TODO[PLATFORM]: đổi MemorySaver sang PostgresSaver để checkpoint bền vững
    return g.compile(checkpointer=MemorySaver())


@lru_cache(maxsize=1)
def get_graph():
    return build_graph()
