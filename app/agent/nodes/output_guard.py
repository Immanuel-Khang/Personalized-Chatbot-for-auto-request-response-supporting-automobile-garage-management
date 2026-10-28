"""
[TRACK B] Output Guard: kiểm tra câu trả lời đã ghép (compose) trước khi gửi khách.
- Cấm hứa giảm giá/quà/cam kết.
- Cấm số tiền không đến từ tool (allowed_amounts gộp từ mọi agent trong lượt).
Vi phạm -> bỏ toàn bộ bản nháp, chuyển người (ghi thêm lý do nếu agent đã yêu cầu chuyển người).
TODO[AGENT]: vi phạm -> cho LLM sinh lại 1 lần trước khi chuyển người (đỡ phình số ticket).
"""
import re

from app.agent.state import AgentState

FORBIDDEN = re.compile(r"(giảm\s*\d+\s*%|tặng|miễn phí|cam kết|đảm bảo giá)", re.IGNORECASE)
AMOUNT = re.compile(r"\d[\d\.]*\s*(?:đồng|triệu|tỷ|vnđ)", re.IGNORECASE)


def _violation(draft: str, allowed: set[str]) -> str | None:
    if FORBIDDEN.search(draft):
        return "OUTPUT_GUARD: cam kết/giảm giá"
    # Chỉ kiểm tra số tiền khi allowed_amounts không rỗng, và chỉ dạng chuẩn "X.XXX.XXX đồng"
    # (định dạng fmt_vnd, ≥ 9 chữ số) – bỏ qua cách viết tắt (800 triệu) vì đó là ngân sách khách tự nêu.
    if allowed:
        for amount in AMOUNT.findall(draft):
            amount_str = amount.strip()
            digits = re.sub(r"[^\d]", "", amount_str.split()[0])
            if len(digits) >= 9 and amount_str not in allowed:
                return f"OUTPUT_GUARD: số tiền lạ ({amount_str})"
    return None


def output_guard(state: AgentState) -> dict:
    update: dict = {"trace": ["output_guard"]}
    reason = _violation(state.get("draft_reply", ""), set(state.get("allowed_amounts", [])))
    if reason:
        previous = state.get("handover_reason") if state.get("needs_human") else None
        update.update(needs_human=True, draft_reply="",
                      handover_reason=f"{previous}; {reason}" if previous else reason)
    return update
