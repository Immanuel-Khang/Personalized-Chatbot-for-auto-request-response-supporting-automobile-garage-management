from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from app.agent.graph import get_graph
from app.agent.state import AgentState


def memory_checkpoint(session_id: str) -> dict:
    """Read the small memory cursor from the thread checkpoint without loading DB messages."""
    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})
    snapshot = get_graph().get_state(config)
    values = snapshot.values or {}
    return {key: values.get(key) for key in (
        "conversation_summary", "summarized_messages", "summarized_through_message_id", "turn")}


def run_turn(session_id: str, customer_id: int | None, text: str, negotiation_active: bool = False,
             mode: str = "BOT", history: list[dict] | None = None, profile: dict | None = None,
             conversation_summary: str | None = None, summarized_through_message_id: int | None = None,
             turn: int | None = None) -> dict:
    """Run one chat turn; LangGraph checkpoint owns cross-turn workflow state."""
    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})
    turn_input = cast(AgentState, {
        "session_id": session_id, "customer_id": customer_id, "user_text": text, "history": history or [],
        "customer_profile": profile,
        "trace": Overwrite([]), "answers": Overwrite([]), "allowed_amounts": Overwrite([]),
        "task_results": Overwrite([]), "waiting_tasks": [], "dependency_phase": None,
        "dependency_task_ids": [], "verified_vehicle": None,
        "conversation_summary": conversation_summary, "summarized_messages": 0,
        "summarized_through_message_id": summarized_through_message_id, "turn": turn,
        "conversation_interpretation": None, "rejected_slots": [],
        "negotiation_active": negotiation_active, "mode": mode,
        "draft_reply": "", "needs_human": False, "handover_reason": None,
        "set_mode": None, "route": "", "route_reason": None, "safety": False,
        "plan": [], "task_ids": [], "deferred_workflow": None,
        "validation_feedback": None, "validation_attempts": 0,
    })
    return get_graph().invoke(turn_input, config)
