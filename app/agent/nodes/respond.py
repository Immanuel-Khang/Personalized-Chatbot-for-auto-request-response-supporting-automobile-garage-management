from app.agent.state import AgentState


def respond(state: AgentState) -> dict:
    return {"final_reply": state.get("draft_reply", ""), "trace": ["respond"]}
