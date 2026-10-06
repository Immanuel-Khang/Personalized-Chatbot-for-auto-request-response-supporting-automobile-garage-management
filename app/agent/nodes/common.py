from app.agent.state import AgentState


def step(state: AgentState, name: str) -> list[str]:
    """Ghi lại node vừa đi qua vào trace."""
    return state.get("trace", []) + [name]
