from langgraph.types import Overwrite

from app.agent.graph import get_graph


def run_turn(conversation_id: int, customer_id: int, text: str, negotiation_active: bool = False,
             mode: str = "BOT") -> dict:
    """Chạy 1 lượt chat. thread_id = conversation_id nên slot được giữ qua các lượt.
    negotiation_active đọc từ bảng conversations (nguồn sự thật cho cờ sticky)."""
    config = {"configurable": {"thread_id": str(conversation_id)}}
    turn_input = {  # reset các trường theo từng lượt; key có reducer phải Overwrite, không thì cộng dồn
        "conversation_id": conversation_id, "customer_id": customer_id, "user_text": text,
        "trace": Overwrite([]), "answers": Overwrite([]), "allowed_amounts": Overwrite([]),
        "tasks": [], "plan": [], "planned_agents": [], "draft_reply": "", "needs_human": False,
        "handover_reason": None, "set_mode": None, "route": "", "safety": False, "slots_updated": False,
        "negotiation_active": negotiation_active, "mode": mode,
    }
    return get_graph().invoke(turn_input, config)
