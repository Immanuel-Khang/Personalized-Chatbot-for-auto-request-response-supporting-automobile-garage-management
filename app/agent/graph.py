"""
[TRACK B] Lắp ráp LangGraph:
preprocess -> policy_guard -> {human_handover | router}
router -(Send)-> các agent chỉ đọc song song (sales, knowledge, after_sales) + tối đa 1 agent ghi (contract | appointment)
mọi agent -> compose (ghép theo thứ tự task) -> output_guard -> {respond | human_handover}; human_handover -> respond
Thêm node mới: viết file trong nodes/, add_node ở đây, khai báo READ_ONLY hoặc WRITERS trong router.
"""
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.after_sales import after_sales
from app.agent.nodes.appointment import appointment
from app.agent.nodes.compose import compose
from app.agent.nodes.contract import contract
from app.agent.nodes.conversation import conversation
from app.agent.nodes.human_handover import human_handover
from app.agent.nodes.knowledge import knowledge
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.policy_guard import policy_guard
from app.agent.nodes.preprocess import preprocess
from app.agent.nodes.respond import respond
from app.agent.nodes.router import READ_ONLY, WRITERS, dispatch, router
from app.agent.nodes.sales import sales
from app.agent.state import AgentState

SPECIALISTS = {"sales": sales, "knowledge": knowledge, "after_sales": after_sales,
               "contract": contract, "appointment": appointment, "conversation": conversation}
assert set(SPECIALISTS) == set(READ_ONLY) | set(WRITERS)


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [
        ("preprocess", preprocess), ("policy_guard", policy_guard), ("router", router),
        *SPECIALISTS.items(), ("compose", compose), ("output_guard", output_guard),
        ("human_handover", human_handover), ("respond", respond),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "policy_guard")
    g.add_conditional_edges(
        "policy_guard", lambda s: s["route"],
        {"human": "human_handover", "router": "router"},
    )
    g.add_conditional_edges("router", dispatch, [*SPECIALISTS, "compose"])
    for name in SPECIALISTS:
        g.add_edge(name, "compose")  # compose chạy sau khi mọi nhánh song song xong (cùng superstep)
    g.add_edge("compose", "output_guard")
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
