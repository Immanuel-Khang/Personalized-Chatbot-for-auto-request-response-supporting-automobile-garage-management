"""[TRACK B] Conversation Agent: xử lý greeting, out-of-scope, cancel, và truy vấn thông tin từ hội thoại.
Thay thế các câu trả lời cứng bằng LLM có ngữ cảnh. Chỉ đọc, không ghi dữ liệu."""
from app.agent.llm import generate
from app.agent.nodes.common import answer, step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import Intent
from app.services import get_services

CONVERSATION_SYSTEM_PROMPT = """\
Bạn là trợ lý thân thiện của xưởng Foton. Nhiệm vụ của bạn:
1. Chào hỏi ấm áp khi khách chào
2. Hướng dẫn lịch sự khi khách hỏi ngoài phạm vi (gợi ý: tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử, kiến thức kỹ thuật)
3. Xác nhận khi khách muốn hủy yêu cầu
4. Trả lời câu hỏi về thông tin khách đã cung cấp trước đó (tên, số điện thoại)

QUY TẮC:
- Luôn trả lời bằng tiếng Việt
- Ngắn gọn, thân thiện
- KHÔNG tự hỏi thông tin khách chưa cần (số điện thoại, ngày giờ) - chỉ trả lời phần được giao
- Nếu khách hỏi về thông tin cá nhân mà không có trong hồ sơ, nói thật là chưa có thông tin đó
- Hotline hỗ trợ: {hotline}
"""


def conversation(state: AgentState) -> dict:
    """Xử lý greeting, other, và các câu hỏi về ngữ cảnh hội thoại."""
    update: dict = {"trace": step(state, "conversation")}
    intents = state.get("intents", [])
    question = state.get("task_text") or state.get("user_text", "")

    # Lấy thông tin khách và lịch sử hội thoại
    session_id = state.get("session_id", "")
    profile_svc = get_services().profile
    profile = profile_svc.get_profile(session_id)
    history = profile_svc.get_conversation_summary(session_id, limit=8)

    # Xác định ngữ cảnh
    intent_context = ""
    if Intent.GREETING.value in intents:
        intent_context = "Khách đang chào hỏi."
    elif Intent.OTHER.value in intents:
        intent_context = "Khách hỏi ngoài phạm vi. Hãy hướng dẫn lịch sự về các chủ đề bạn có thể hỗ trợ."
    elif state.get("active_flow") or state.get("pending_intent"):
        intent_context = "Khách muốn hủy yêu cầu đang xử lý. Xác nhận đã hủy và hỏi có cần gì khác."

    # Chuẩn bị ngữ cảnh cho LLM
    profile_text = ""
    if profile.name or profile.phone:
        profile_text = f"Thông tin khách đã lưu: Tên: {profile.name or 'chưa có'}, SĐT: {profile.phone or 'chưa có'}"
        if profile.verified:
            profile_text += " (đã xác thực)"
    else:
        profile_text = "Chưa có thông tin khách hàng trong hồ sơ."

    context = f"""
{intent_context}

Lịch sử hội thoại gần đây:
{history}

{profile_text}

Tin nhắn mới của khách: {question}
"""

    # Gọi LLM
    reply = generate(
        CONVERSATION_SYSTEM_PROMPT.format(hotline=settings.hotline),
        context
    )

    # Fallback nếu LLM không khả dụng
    if not reply:
        if Intent.GREETING.value in intents:
            reply = "Chào bạn! Mình là trợ lý của xưởng Foton. Mình có thể tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử hoặc giải đáp thắc mắc kỹ thuật."
        elif Intent.OTHER.value in intents:
            reply = "Mình chưa hiểu rõ ý bạn. Bạn có thể hỏi về giá xe, đặt lịch bảo dưỡng/lái thử, hoặc kiến thức kỹ thuật nhé."
        else:
            reply = "Mình đã dừng yêu cầu đang chờ. Bạn cần hỗ trợ gì khác cứ nhắn nhé."

    update["answers"] = [answer(state, "conversation", reply)]
    return update
