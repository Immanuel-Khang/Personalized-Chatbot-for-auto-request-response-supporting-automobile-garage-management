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


def _digits(amount: str) -> str:
    """'425.000.000 VNĐ' / '425.000.000 đồng' -> '425000000' (so sánh theo giá trị, không theo cách viết)."""
    return re.sub(r"[^\d]", "", amount.split()[0])


def violation(draft: str, allowed: set[str]) -> str | None:
    if FORBIDDEN.search(draft):
        return "OUTPUT_GUARD: cam kết/giảm giá"
    # Chỉ kiểm tra số tiền khi allowed_amounts không rỗng, và chỉ dạng đầy đủ (>= 9 chữ số, vd 425.000.000 đồng/VNĐ)
    # – bỏ qua cách viết tắt (800 triệu) vì đó là ngân sách khách tự nêu. So khớp theo giá trị số, không theo đuôi đơn vị.
    if allowed:
        allowed_values = {_digits(item) for item in allowed}
        for amount in AMOUNT.findall(draft):
            amount = amount.strip()
            digits = _digits(amount)
            if len(digits) >= 9 and digits not in allowed_values:
                return f"OUTPUT_GUARD: số tiền lạ ({amount})"
    return None


def output_guard(state: AgentState) -> dict:
    update: dict = {"trace": ["output_guard"]}
    reason = violation(state.get("draft_reply", ""), set(state.get("allowed_amounts", [])))
    if reason:
        previous = state.get("handover_reason") if state.get("needs_human") else None
        update.update(needs_human=True, draft_reply="",
                      handover_reason=f"{previous}; {reason}" if previous else reason)
    return update
