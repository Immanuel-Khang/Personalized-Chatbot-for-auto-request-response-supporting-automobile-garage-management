from app.agent.state import AgentState


def step(state: AgentState, name: str) -> list[str]:
    """Node vừa đi qua; trace có reducer cộng dồn nên các nhánh song song không ghi đè nhau."""
    return [name]


def answer(state: AgentState, agent: str, text: str = "", needs_human: bool = False,
           handover_reason: str | None = None) -> dict:
    """Phần trả lời của một specialist; compose ghép theo task_order rồi mới qua Output Guard."""
    return {"agent": agent, "order": state.get("task_order", 0), "text": text,
            "needs_human": needs_human, "handover_reason": handover_reason}
