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
    # Chỉ kiểm tra số tiền khi allowed_amounts không rỗng.
    # Chỉ chặn số tiền ở dạng chuẩn "X.XXX.XXX đồng" (định dạng fmt_vnd) –
    # bỏ qua các cách viết tắt (800 triệu, 1.5 tỷ) vì đó là ngân sách khách
    # tự nêu, không phải giá xe do bot tự bịa.
    if allowed:
        for amount in AMOUNT.findall(draft):
            amount_str = amount.strip()
            # Chỉ kiểm tra số có dạng đầy đủ "ddd.ddd.ddd đồng" (≥ 9 chữ số trước đơn vị)
            digits = re.sub(r"[^\d]", "", amount_str.split()[0])
            if len(digits) >= 9 and amount_str not in allowed:
                return {**update, "needs_human": True,
                        "handover_reason": f"OUTPUT_GUARD: số tiền lạ ({amount_str})"}
    return update
