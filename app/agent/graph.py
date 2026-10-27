"""LangGraph assembly (matches the documented architecture):
input -> preprocess -> policy_guard -> {human_handover | router}
router -> one specialist directly, or coordinator -> several specialists (parallel, max one writer)
specialists -> validation -> {preprocess (missing info, once) | compose} -> output_guard
output_guard -> {respond | human_handover}; human_handover -> respond -> END.
Persisting messages and workflow state happens in app/conversation.py (DB) and the checkpointer.
Add a node: create nodes/<name>.py, add_node here, declare it in READ_ONLY or WRITERS in router.py.
"""
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.after_sales import after_sales
from app.agent.nodes.appointment import appointment
from app.agent.nodes.compose import compose
from app.agent.nodes.contract import contract
from app.agent.nodes.conversation import conversation
from app.agent.nodes.coordinator import coordinate, coordinator
from app.agent.nodes.human_handover import human_handover
from app.agent.nodes.input import load_context
from app.agent.nodes.knowledge import knowledge
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.policy_guard import policy_guard
from app.agent.nodes.preprocess import preprocess
from app.agent.nodes.respond import respond
from app.agent.nodes.router import READ_ONLY, WRITERS, dispatch, router
from app.agent.nodes.sales import sales
from app.agent.nodes.validation import validation
from app.agent.state import AgentState

SPECIALISTS = {"sales": sales, "knowledge": knowledge, "after_sales": after_sales,
               "contract": contract, "appointment": appointment, "conversation": conversation}
assert set(SPECIALISTS) == set(READ_ONLY) | set(WRITERS)


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [
        ("input", load_context), ("preprocess", preprocess), ("policy_guard", policy_guard),
        ("router", router), ("coordinator", coordinator), *SPECIALISTS.items(),
        ("validation", validation), ("compose", compose), ("output_guard", output_guard),
        ("human_handover", human_handover), ("respond", respond),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "input")
    g.add_edge("input", "preprocess")
    g.add_edge("preprocess", "policy_guard")
    g.add_conditional_edges("policy_guard", lambda s: s["route"],
                            {"human": "human_handover", "router": "router"})
    g.add_conditional_edges("router", dispatch, [*SPECIALISTS, "coordinator"])
    g.add_conditional_edges("coordinator", coordinate, list(SPECIALISTS))
    for name in SPECIALISTS:
        g.add_edge(name, "validation")  # runs once all parallel branches of the superstep finish
    g.add_conditional_edges("validation", lambda s: "preprocess" if s.get("route") == "retry" else "compose",
                            {"preprocess": "preprocess", "compose": "compose"})
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
