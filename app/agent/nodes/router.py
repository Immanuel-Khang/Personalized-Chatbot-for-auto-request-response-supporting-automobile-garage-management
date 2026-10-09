"""[TRACK B] Intent Router. Có nhánh mặc định (greeting/ngoài phạm vi -> trả lời thẳng).
DISCOUNT không tới đây (policy_guard đã chuyển người). MAINTENANCE -> knowledge."""
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent

PRIORITY = [Intent.CONTRACT, Intent.APPOINTMENT, Intent.MAINTENANCE, Intent.SALES, Intent.KNOWLEDGE]
GREETING_REPLY = "Chào bạn! Mình là trợ lý của xưởng Foton. Mình có thể tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử hoặc giải đáp thắc mắc kỹ thuật."
OUT_OF_SCOPE_REPLY = "Mình chưa hiểu rõ ý bạn. Bạn có thể hỏi về giá xe, đặt lịch bảo dưỡng/lái thử, hoặc kiến thức kỹ thuật nhé."


def router(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "router")}
    if pick_agent(state) == "respond":
        # directly navigate to respond node if greeting or out of scope intent is detected
        is_greeting = Intent.GREETING.value in state.get("intents", [])
        update["draft_reply"] = GREETING_REPLY if is_greeting else OUT_OF_SCOPE_REPLY
    return update


def pick_agent(state: AgentState) -> str:
    intents = state.get("intents", [])
    for i in PRIORITY:
        if i.value in intents:
            # MAINTENANCE → route vào knowledge (kiến thức bảo dưỡng)
            if i == Intent.MAINTENANCE:
                return "knowledge"
            return i.value.lower()
    return "respond"
