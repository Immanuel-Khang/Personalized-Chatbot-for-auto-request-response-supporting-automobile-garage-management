"""[TRACK C] Contract Agent: slot-filling hợp đồng mua xe.
Port từ codebase hiện tại: thu thập name, phone, address, car_model, decided_price."""
import json

from app.agent.llm import generate
from app.agent.nodes.common import step
from app.agent.state import AgentState

# Các trường cần thiết để hoàn tất hợp đồng (port từ contract_node)
REQUIRED_FIELDS = {
    "customer_name": "Họ và tên",
    "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ nhận xe / hộ khẩu",
    "car_model": "Dòng xe chọn mua",
    "decided_price": "Mức giá chốt hợp đồng (VNĐ)",
}

CONTRACT_SYSTEM_PROMPT = """\
Bạn là chuyên viên pháp lý và thủ tục hợp đồng mua xe Toyota.
QUY TẮC:
1. Nếu còn thông tin thiếu, chúc mừng khách đã chốt xe và nhẹ nhàng xin nốt các thông tin còn thiếu.
2. Nếu đã ĐỦ TẤT CẢ thông tin, tóm tắt lại toàn bộ hợp đồng (Tên, SĐT, Địa chỉ, Dòng xe, Giá chốt)
   và hẹn ngày ký kết/bàn giao xe.
3. Luôn trả lời bằng tiếng Việt, lịch sự và chuyên nghiệp.
"""


def contract(state: AgentState) -> dict:
    slots = state.get("slots", {})

    # Tìm thông tin còn thiếu
    missing_fields = [label for key, label in REQUIRED_FIELDS.items() if not slots.get(key)]
    present_fields = {label: slots.get(key) for key, label in REQUIRED_FIELDS.items() if slots.get(key)}

    if missing_fields:
        # Dùng LLM để hỏi thông tin thiếu một cách tự nhiên
        llm_reply = generate(
            CONTRACT_SYSTEM_PROMPT,
            f"Câu hỏi/tin nhắn của khách: {state['user_text']}\n\n"
            f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(present_fields, ensure_ascii=False)}\n\n"
            f"⚠️ THÔNG TIN CÒN THIẾU: {missing_fields}\n\n"
            f"Hãy chúc mừng khách và xin nốt các thông tin còn thiếu."
        )
        if llm_reply:
            reply = llm_reply
        else:
            # Fallback
            missing_str = ", ".join(missing_fields)
            reply = f"🎉 Chúc mừng bạn đã quyết định chốt xe!\nĐể hoàn tất hợp đồng, mình cần thêm: {missing_str}."
    else:
        # Đã đủ thông tin → tóm tắt hợp đồng
        llm_reply = generate(
            CONTRACT_SYSTEM_PROMPT,
            f"Câu hỏi/tin nhắn của khách: {state['user_text']}\n\n"
            f"📋 THÔNG TIN ĐẦY ĐỦ: {json.dumps(present_fields, ensure_ascii=False)}\n\n"
            f"Đã đủ tất cả thông tin. Hãy tóm tắt hợp đồng và hẹn ngày ký kết."
        )
        if llm_reply:
            reply = llm_reply
        else:
            info_str = "\n".join(f"  • {label}: {value}" for label, value in present_fields.items())
            reply = f"📄 TÓM TẮT HỢP ĐỒNG:\n{info_str}\n\nMình sẽ liên hệ bạn để hẹn ngày ký kết và bàn giao xe. Cảm ơn bạn!"

    return {"draft_reply": reply, "active_flow": "contract", "trace": step(state, "contract")}
