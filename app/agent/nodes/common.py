import re

from app.agent.state import AgentState

# Quy tắc văn phong chung cho mọi prompt sinh câu trả lời
STYLE_RULES = """\
- KHÔNG mở đầu bằng lời chào hay gọi tên khách (VD "Chào Khang,") trừ khi khách vừa chào; đi thẳng vào nội dung.
- Nếu không chắc khách muốn gì, hỏi lại ngắn gọn để làm rõ; KHÔNG tự suy đoán.
- Trả lời thẳng câu khách vừa hỏi trước, sau đó mới gợi ý MỘT bước tiếp theo hữu ích (nếu có); không bắt câu nào cũng kết bằng câu hỏi.
- Chỉ hỏi thông tin thật sự cần cho việc đang làm; KHÔNG hỏi lại thông tin khách đã cung cấp.
- Gợi ý một việc (đặt lịch, lái thử, hợp đồng) KHÔNG phải là đã làm; KHÔNG nói đã đặt/xác nhận/áp dụng khi hệ thống chưa báo thành công.
- Chỉ dùng dữ liệu được cung cấp (catalog, tài liệu, kết quả hệ thống); thiếu dữ liệu thì nói rõ phần nào cần nhân viên xác nhận.
- Lịch sự, tự nhiên như nhân viên cố vấn dịch vụ; không lộ tên luồng, intent hay lỗi kỹ thuật nội bộ.
- Nếu các mẫu xe, bảng so sánh hoặc thông số kỹ thuật đã được trình bày ở các lượt trả lời trước đó trong lịch sử trò chuyện, TUYỆT ĐỐI KHÔNG liệt kê hay trình bày lại từ đầu. Đi thẳng vào giải đáp, phân tích điểm khác biệt cho chi tiết mới mà khách vừa nhắn.
"""

# "Chào Khang," / "Xin chào bạn!" / "Chào anh/chị." ở đầu câu trả lời
GREETING_PREFIX = re.compile(r"^\s*(?:xin\s+)?chào(?:\s+[^\s,.!\n]+){0,3}\s*[,.!]+\s*", re.IGNORECASE)


def step(state: AgentState, name: str) -> list[str]:
    """Node vừa đi qua; trace có reducer cộng dồn nên các nhánh song song không ghi đè nhau."""
    return [name]


def answer(state: AgentState, agent: str, text: str = "", needs_human: bool = False,
           handover_reason: str | None = None) -> dict:
    """Phần trả lời của một specialist; compose ghép theo task_order rồi mới qua Output Guard."""
    return {"agent": agent, "order": state.get("task_order", 0), "text": text,
            "needs_human": needs_human, "handover_reason": handover_reason}


def strip_greeting(text: str) -> str:
    """Bỏ lời chào ở đầu câu trả lời (dùng khi hội thoại đã bắt đầu)."""
    stripped = GREETING_PREFIX.sub("", text, count=1)
    if not stripped.strip() or stripped == text:
        return text
    return stripped[0].upper() + stripped[1:]
