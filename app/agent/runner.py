from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from app.agent.graph import get_graph
from app.agent.state import AgentState


def run_turn(session_id: str, customer_id: int | None, text: str, negotiation_active: bool = False,
             mode: str = "BOT", history: list[dict] | None = None) -> dict:
    """Run one chat turn; LangGraph checkpoint owns cross-turn workflow state."""
    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})
    turn_input = cast(AgentState, {
        "session_id": session_id, "customer_id": customer_id, "user_text": text, "history": history or [],
        "trace": Overwrite([]), "answers": Overwrite([]), "allowed_amounts": Overwrite([]),
        "conversation_interpretation": None,
        "negotiation_active": negotiation_active, "mode": mode,
        "draft_reply": "", "needs_human": False, "handover_reason": None,
        "set_mode": None, "route": "", "route_reason": None, "safety": False,
    })
    return get_graph().invoke(turn_input, config)
