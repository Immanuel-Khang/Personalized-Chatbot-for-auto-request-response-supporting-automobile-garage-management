"""Result Validation / Grounding: check specialist results before composing the reply.

- Missing required info: a specialist produced no usable result although it did not ask for
  a human (e.g. a product reference that could not be resolved). The turn goes back to
  Preprocessing once, with feedback, instead of sending an empty or invented answer.
- Grounding: a reply may only claim a booking/contract when this turn's writer reported
  success (``executed`` on its answer); otherwise the specialist is asked to hand over.
- A deferred second writer becomes an open OFFER, so a short "ok" next turn resumes it.
"""
import uuid

from langgraph.types import Overwrite

from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import InteractionKind, InteractionStatus, PendingInteraction

MAX_ATTEMPTS = 1
CLAIMS = ("đã xác nhận lịch", "đã đặt lịch", "đã ghi nhận lịch hẹn", "hợp đồng đã được tạo", "đã ký hợp đồng")


def _missing_info(state: AgentState) -> str | None:
    answers = state.get("answers") or []
    empty = [a["agent"] for a in answers if not a.get("needs_human") and not (a.get("text") or "").strip()]
    if empty:
        return f"Các agent {', '.join(empty)} không có kết quả; kiểm tra lại task, slot và tham chiếu."
    return None


def _ungrounded_claim(answer: dict) -> bool:
    text = (answer.get("text") or "").casefold()
    return any(claim in text for claim in CLAIMS) and not answer.get("executed")


def validation(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "validation")}
    attempts = state.get("validation_attempts", 0)
    if (feedback := _missing_info(state)) and attempts < MAX_ATTEMPTS:
        return {**update, "validation_feedback": feedback, "validation_attempts": attempts + 1,
                "answers": Overwrite([]), "allowed_amounts": Overwrite([]), "route": "retry"}

    answers = []
    for item in state.get("answers") or []:
        if _ungrounded_claim(item):
            item = {**item, "text": "", "needs_human": True, "handover_reason": "VALIDATION: unconfirmed action claim"}
        answers.append(item)
    update.update(answers=Overwrite(answers), route="compose", validation_feedback=None)

    if deferred := state.get("deferred_workflow"):
        offer = PendingInteraction(
            interaction_id=f"{deferred['workflow_type']}_{uuid.uuid4().hex[:8]}", kind=InteractionKind.OFFER,
            text=f"Tiếp tục {deferred['workflow_type']}", status=InteractionStatus.OPEN,
            metadata={"workflow": deferred["workflow_type"]},
        )
        update["pending_interactions"] = [*(state.get("pending_interactions") or []), offer]
        update["deferred_workflow"] = None
    return update
