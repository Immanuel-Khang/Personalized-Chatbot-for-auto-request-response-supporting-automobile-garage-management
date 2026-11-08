from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from app.agent.graph import get_graph
from app.agent.state import AgentState


def run_turn(session_id: str, customer_id: int | None, text: str, negotiation_active: bool = False,
             mode: str = "BOT", history: list[dict] | None = None, profile: dict | None = None) -> dict:
    """Run one chat turn; LangGraph checkpoint owns cross-turn workflow state."""
    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})
    turn_input = cast(AgentState, {
        "session_id": session_id, "customer_id": customer_id, "user_text": text, "history": history or [],
        "customer_profile": profile,
        "trace": Overwrite([]), "answers": Overwrite([]), "allowed_amounts": Overwrite([]),
        "conversation_interpretation": None, "rejected_slots": [],
        "negotiation_active": negotiation_active, "mode": mode,
        "draft_reply": "", "needs_human": False, "handover_reason": None,
        "set_mode": None, "route": "", "route_reason": None, "safety": False,
        "plan": [], "task_ids": [], "deferred_workflow": None,
        "validation_feedback": None, "validation_attempts": 0,
    })
    return get_graph().invoke(turn_input, config)


def update_known_facts(session_id: str, values: dict) -> bool:
    """Ghi fact đã được nhân viên duyệt vào checkpoint của phiên để bot dùng ở lượt sau (giá chốt, tên/SĐT/địa chỉ,
    ghi chú). False nếu phiên chưa có checkpoint (chưa từng chạy lượt bot nào, nên chưa có state để cập nhật).
    Gọi trong khóa phiên của conversation.py để không đè lên một lượt bot đang chạy."""
    from app.contracts.schemas import FactSource, KnownFact

    graph = get_graph()
    config = cast(RunnableConfig, {"configurable": {"thread_id": session_id}})
    snapshot = graph.get_state(config)
    if not snapshot.values:
        return False
    facts = dict(snapshot.values.get("known_facts") or {})
    for key, value in values.items():
        facts[key] = KnownFact(key=key, value=value, source=FactSource.APPLICATION_VERIFIED)
    graph.update_state(config, {"known_facts": facts}, as_node="respond")
    return True
