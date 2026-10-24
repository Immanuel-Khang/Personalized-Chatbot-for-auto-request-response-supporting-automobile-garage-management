from langgraph.types import Overwrite

from app.agent.graph import get_graph


def run_turn(session_id: str, customer_id: int | None, text: str, negotiation_active: bool = False,
             mode: str = "BOT", history: list[dict] | None = None) -> dict:
    """Chạy 1 lượt chat. thread_id = session_id nên slot được giữ qua các lượt.
    negotiation_active đọc từ bảng chat_sessions (nguồn sự thật cho cờ sticky).
    history: tin user/ai trước lượt này (bảng messages), không chứa `text`."""
    config = {"configurable": {"thread_id": session_id}}
    turn_input = {  # reset các trường theo từng lượt; key có reducer phải Overwrite, không thì cộng dồn
        "session_id": session_id, "customer_id": customer_id, "user_text": text, "history": history or [],
        "trace": Overwrite([]), "answers": Overwrite([]), "allowed_amounts": Overwrite([]),
        "tasks": [], "plan": [], "planned_agents": [], "draft_reply": "", "needs_human": False,
        "handover_reason": None, "set_mode": None, "route": "", "safety": False, "slots_updated": False,
        "negotiation_active": negotiation_active, "mode": mode, "conversation_reason": None,
        "sales_signals": {}, "followup": {},
    }
    return get_graph().invoke(turn_input, config)
