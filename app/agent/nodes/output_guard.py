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
    # Các số tiền "nhỏ" (< 10 triệu) là chi phí phụ/ngân sách khách tự nêu → bỏ qua,
    # chỉ chặn số tiền lớn (giá xe/dịch vụ) mà không có trong danh sách được phép.
    PRICE_RE = re.compile(r"(\d[\d\.]*)\s*(đồng|triệu|tỷ|vnđ)", re.IGNORECASE)
    for m in PRICE_RE.finditer(draft):
        raw_num, unit = m.group(1).replace(".", ""), m.group(2).lower()
        try:
            value = float(raw_num)
            if unit in ("triệu", "trieu"):
                value *= 1_000_000
            elif unit in ("tỷ", "ty"):
                value *= 1_000_000_000
        except ValueError:
            continue
        # Bỏ qua số tiền nhỏ (< 10 triệu) – chi phí phụ hoặc ngân sách khách tự nêu
        if value < 10_000_000:
            continue
        amount_str = m.group(0).strip()
        if len(allowed) > 0 and amount_str not in allowed:
            return {**update, "needs_human": True, "handover_reason": f"OUTPUT_GUARD: số tiền lạ ({amount_str})"}
    return update
