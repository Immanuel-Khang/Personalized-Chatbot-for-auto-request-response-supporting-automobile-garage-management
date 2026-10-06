from app.agent.nodes.common import step
from app.agent.state import AgentState


def respond(state: AgentState) -> dict:
    return {"final_reply": state.get("draft_reply", ""), "trace": step(state, "respond")}
