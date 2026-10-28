"""LangGraph assembly (matches the documented architecture):
input -> preprocess -> policy_guard -> {human_handover | router}
router -> one specialist directly, or coordinator -> several specialists (parallel, max one writer)
specialists -> validation -> {preprocess (missing info, once) | compose} -> output_guard
output_guard -> {respond | human_handover}; human_handover -> respond -> END.
Save: messages go to the DB in app/conversation.py; workflow state (facts, pending, workflow) is the
LangGraph checkpoint, stored in Postgres so it survives restarts.
Add a node: create nodes/<name>.py, add_node here, declare it in READ_ONLY or WRITERS in router.py.
"""
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
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
from app.contracts import schemas
from app.config import settings

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
    return g.compile(checkpointer=_checkpointer())

# Pydantic models/enums kept in AgentState; LangGraph only deserializes allowlisted types from a checkpoint.
CHECKPOINT_TYPES = [("app.contracts.schemas", name) for name in (
    "ActiveWorkflow", "ConversationInterpretation", "EntityReference", "FactSource", "Intent", "InteractionKind",
    "InteractionStatus", "KnownFact", "PendingInteraction", "PolicySignals", "SlotOperation", "SlotUpdate",
    "TaskInterpretation", "TaskRelationship", "WorkflowStatus")]
assert all(hasattr(schemas, name) for _, name in CHECKPOINT_TYPES)


def _checkpointer():
    """Postgres checkpoint in the app database; in-memory for non-Postgres URLs (e.g. SQLite tests)."""
    url = settings.database_url
    if not url.startswith("postgresql"):
        return MemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver
    conn = Connection.connect(url.replace("+psycopg", "", 1), autocommit=True, prepare_threshold=0)
    saver = PostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))
    saver.setup()
    return saver


@lru_cache(maxsize=1)
def get_graph():
    return build_graph()
