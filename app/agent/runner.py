from app.agent.graph import get_graph


def run_turn(conversation_id: int, customer_id: int, text: str) -> dict:
    """Chạy 1 lượt chat. thread_id = conversation_id nên slot/negotiation_active được giữ qua các lượt."""
    config = {"configurable": {"thread_id": str(conversation_id)}}
    turn_input = {  # reset các trường theo từng lượt
        "conversation_id": conversation_id, "customer_id": customer_id, "user_text": text,
        "trace": [], "draft_reply": "", "allowed_amounts": [], "needs_human": False,
        "handover_reason": None, "set_mode": None, "route": "", "safety": False,
        "discount_auto_approved": False,
    }
    return get_graph().invoke(turn_input, config)
