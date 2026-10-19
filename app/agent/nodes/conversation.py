"""[TRACK B] Conversation Agent: xử lý greeting, out-of-scope, cancel, hỏi lại khi chưa rõ ý,
và truy vấn thông tin từ hội thoại. Chỉ đọc, không ghi dữ liệu."""
from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, conversation_history, step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import Intent
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal

CONVERSATION_SYSTEM_PROMPT = """\
Bạn là trợ lý thân thiện của xưởng Foton. Nhiệm vụ của bạn:
1. Chào lại ngắn gọn khi khách chào
2. Hướng dẫn lịch sự khi khách hỏi ngoài phạm vi (gợi ý: tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử, kiến thức kỹ thuật)
3. Xác nhận khi khách muốn hủy yêu cầu
4. Trả lời câu hỏi về thông tin khách đã cung cấp trước đó (tên, số điện thoại)
5. Khi chưa hiểu khách muốn gì: hỏi lại để làm rõ, có thể nêu cách bạn đang hiểu để khách xác nhận

QUY TẮC:
- Luôn trả lời bằng tiếng Việt
- Ngắn gọn, tự nhiên như đang nhắn tin tiếp trong cùng cuộc trò chuyện
- Đọc lịch sử hội thoại: tin nhắn mới thường nói tiếp về chủ đề vừa trao đổi (VD khách báo thông tin bot vừa đưa bị sai)
- Khách có thể gõ sai dấu/chính tả; chỉ hiểu theo nghĩa khi chắc chắn, nếu không thì hỏi lại
- KHÔNG tự hỏi thông tin khách chưa cần (số điện thoại, ngày giờ) và KHÔNG tự mở yêu cầu đặt lịch / mua xe
- Nếu khách hỏi về thông tin cá nhân mà không có trong hồ sơ, nói thật là chưa có thông tin đó
- Hotline hỗ trợ: {hotline}
{style}"""

INTENT_CONTEXT = {
    "greeting": "Khách đang chào hỏi.",
    "cancel": "Khách muốn hủy yêu cầu đang xử lý. Xác nhận đã dừng và hỏi có cần gì khác.",
    "clarify": "Bạn chưa hiểu rõ khách muốn gì. Hỏi lại ngắn gọn để làm rõ, KHÔNG tự đoán và KHÔNG tự thực hiện yêu cầu.",
    "other": ("Tin nhắn chưa thuộc chủ đề nào bạn xử lý được. Nếu đó là câu nói tiếp chủ đề trước thì trả lời theo ngữ cảnh; "
              "nếu chưa rõ ý thì hỏi lại; nếu ngoài phạm vi thì hướng dẫn lịch sự các chủ đề bạn hỗ trợ."),
}

FALLBACK = {
    "greeting": "Chào bạn! Mình là trợ lý của xưởng Foton. Mình có thể tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử hoặc giải đáp thắc mắc kỹ thuật.",
    "cancel": "Mình đã dừng yêu cầu đang chờ. Bạn cần hỗ trợ gì khác cứ nhắn nhé.",
    "clarify": "Mình chưa hiểu rõ ý bạn lắm. Bạn nói rõ hơn giúp mình được không?",
    "other": "Mình chưa hiểu rõ ý bạn. Bạn có thể hỏi về giá xe, đặt lịch bảo dưỡng/lái thử, hoặc kiến thức kỹ thuật nhé.",
}


def _get_customer_profile(session_id: str) -> tuple[str | None, str | None]:
    """Truy vấn thông tin khách hàng từ DB. Trả về (tên, số điện thoại)."""
    db = SessionLocal()
    try:
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not chat_session or not chat_session.customer_id:
            return None, None
        customer = db.query(Customer).filter(Customer.id == chat_session.customer_id).first()
        if not customer:
            return None, None
        return customer.full_name, customer.phone_number
    finally:
        db.close()


def _reason(state: AgentState) -> str:
    """Router quyết định cancel/clarify; còn lại theo intent."""
    if reason := state.get("conversation_reason"):
        return reason
    if Intent.GREETING.value in state.get("intents", []):
        return "greeting"
    return "other"


def conversation(state: AgentState) -> dict:
    """Xử lý greeting, other, cancel, clarify và các câu hỏi về ngữ cảnh hội thoại."""
    update: dict = {"trace": step(state, "conversation")}
    question = state.get("task_text") or state.get("user_text", "")
    reason = _reason(state)

    # Lấy thông tin khách và lịch sử hội thoại từ DB
    session_id = state.get("session_id", "")
    name, phone = _get_customer_profile(session_id)
    history = conversation_history(session_id, limit=8)

    if name or phone:
        profile_text = f"Thông tin khách đã lưu: Tên: {name or 'chưa có'}, SĐT: {phone or 'chưa có'}"
    else:
        profile_text = "Chưa có thông tin khách hàng trong hồ sơ."

    context = f"""
{INTENT_CONTEXT[reason]}

Lịch sử hội thoại gần đây:
{history}

{profile_text}

Tin nhắn mới của khách: {question}
"""

    reply = generate(CONVERSATION_SYSTEM_PROMPT.format(hotline=settings.hotline, style=STYLE_RULES), context)
    update["answers"] = [answer(state, "conversation", reply or FALLBACK[reason])]
    return update
