"""
[TRACK B] Output Guard: chặn câu trả lời vi phạm trước khi gửi khách.
- Cấm hứa giảm giá/quà/cam kết.
- Cấm số tiền không đến từ tool (allowed_amounts).
TODO[AGENT]: vi phạm -> cho LLM sinh lại 1 lần trước khi chuyển người (đỡ phình số ticket).
"""
import re

from app.agent.nodes.common import step
from app.agent.state import AgentState

FORBIDDEN = re.compile(r"(giảm\s*\d+\s*%|tặng|miễn phí|cam kết|đảm bảo giá)", re.IGNORECASE)
AMOUNT = re.compile(r"\d[\d\.]*\s*(?:đồng|triệu|tỷ|vnđ)", re.IGNORECASE)


def output_guard(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "output_guard")}
    if state.get("needs_human"):  # agent đã tự yêu cầu chuyển người
        return update
    draft = state.get("draft_reply", "")
    allowed = set(state.get("allowed_amounts", []))
    if FORBIDDEN.search(draft):
        return {**update, "needs_human": True, "handover_reason": "OUTPUT_GUARD: cam kết/giảm giá"}
    for amount in AMOUNT.findall(draft):
        if amount.strip() not in allowed:
            return {**update, "needs_human": True, "handover_reason": f"OUTPUT_GUARD: số tiền lạ ({amount})"}
    return update
