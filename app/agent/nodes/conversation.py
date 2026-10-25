"""[TRACK B] Conversation Agent: xử lý greeting, out-of-scope, cancel, hỏi lại khi chưa rõ ý,
và truy vấn thông tin từ hội thoại. Chỉ đọc, không ghi dữ liệu."""
from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.nodes.service_type import detect_component
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import InteractionStatus, Intent, PendingInteraction
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal
from app.services import get_services

CONVERSATION_SYSTEM_PROMPT = """\
Bạn là trợ lý thân thiện của xưởng Foton. Nhiệm vụ của bạn:
1. Chào lại ngắn gọn khi khách chào
2. Hướng dẫn lịch sự khi khách hỏi ngoài phạm vi (gợi ý: tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử, kiến thức kỹ thuật)
3. Xác nhận khi khách muốn hủy yêu cầu
4. Trả lời câu hỏi về thông tin khách đã cung cấp trước đó (tên, số điện thoại)
5. Trả lời câu hỏi về dịch vụ / thủ tục dựa trên tài liệu được cung cấp
6. Khi chưa hiểu khách muốn gì: hỏi lại để làm rõ, có thể nêu cách bạn đang hiểu để khách xác nhận

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
    "service_inquiry": ("Khách hỏi xưởng có hỗ trợ một hạng mục sửa chữa/kiểm tra không. Trả lời câu hỏi trước: chỉ khẳng định "
                        "xưởng làm được hạng mục đó khi tài liệu bên dưới nói rõ; nếu không, nói kỹ thuật viên cần kiểm tra/xác nhận "
                        "trực tiếp, KHÔNG khẳng định có hay không. Sau đó gợi ý mang xe qua để kiểm tra. KHÔNG hỏi số điện thoại, "
                        "KHÔNG chẩn đoán chắc chắn nguyên nhân, KHÔNG báo giá khi tài liệu không có giá."),
    "contract_inquiry": ("Khách hỏi thủ tục/giấy tờ mua xe hoặc hợp đồng, CHƯA phải chốt mua. Chỉ trả lời theo tài liệu bên dưới; "
                         "tài liệu không có thì nói nhân viên tư vấn sẽ xác nhận danh mục giấy tờ chính xác. KHÔNG tự liệt kê "
                         "giấy tờ pháp lý, KHÔNG hứa duyệt vay, KHÔNG xin thông tin cá nhân."),
    "decline": ("Khách vừa từ chối câu hỏi/gợi ý cuối của bạn. Ghi nhận ngắn gọn, KHÔNG lặp lại gợi ý đó, "
                "KHÔNG tự mở việc khác; chỉ nói khách cần gì thêm thì cứ nhắn."),
    "clarify_offer": "Câu trả lời của khách có thể ứng với nhiều gợi ý bạn vừa đưa. Hỏi lại đúng một câu: {question}",
    "other": ("Tin nhắn chưa thuộc chủ đề nào bạn xử lý được. Nếu đó là câu nói tiếp chủ đề trước thì trả lời theo ngữ cảnh; "
              "nếu chưa rõ ý thì hỏi lại; nếu ngoài phạm vi thì hướng dẫn lịch sự các chủ đề bạn hỗ trợ."),
}

FALLBACK = {
    "greeting": "Chào bạn! Mình là trợ lý của xưởng Foton. Mình có thể tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử hoặc giải đáp thắc mắc kỹ thuật.",
    "cancel": "Mình đã dừng yêu cầu đang chờ. Bạn cần hỗ trợ gì khác cứ nhắn nhé.",
    "clarify": "Mình chưa hiểu rõ ý bạn lắm. Bạn nói rõ hơn giúp mình được không?",
    "service_inquiry": ("Dạ, về {component} thì kỹ thuật viên bên em cần xem trực tiếp mới xác nhận được hướng xử lý và chi phí ạ. "
                        "Nếu anh/chị muốn, em có thể sắp xếp để anh/chị mang xe qua xưởng kiểm tra."),
    "contract_inquiry": ("Dạ, danh mục giấy tờ cụ thể sẽ do nhân viên tư vấn xác nhận theo hình thức mua của anh/chị ạ. "
                         "Anh/chị cần em chuyển câu hỏi này cho nhân viên tư vấn không?"),
    "decline": "Dạ vâng ạ. Anh/chị cần em hỗ trợ gì thêm thì cứ nhắn nhé.",
    "clarify_offer": "{question}",
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
    """Use router's canonical reason, then infer greeting from canonical tasks."""
    if reason := state.get("route_reason"):
        return reason
    interpretation = state.get("conversation_interpretation")
    if interpretation and any(task.domain == Intent.GREETING for task in interpretation.tasks):
        return "greeting"
    return "other"


def conversation(state: AgentState) -> dict:
    """Xử lý greeting, other, cancel, clarify và các câu hỏi về ngữ cảnh hội thoại."""
    update: dict = {"trace": step(state, "conversation")}
    question = state.get("task_text") or state.get("user_text", "")
    reason = _reason(state)

    # Lấy thông tin khách từ DB (lịch sử hội thoại đi dạng message qua state['history'])
    session_id = state.get("session_id", "")
    name, phone = _get_customer_profile(session_id)

    if name or phone:
        profile_text = f"Thông tin khách đã lưu: Tên: {name or 'chưa có'}, SĐT: {phone or 'chưa có'}"
    else:
        profile_text = "Chưa có thông tin khách hàng trong hồ sơ."

    documents = ""
    if reason in ("service_inquiry", "contract_inquiry"):
        chunks = get_services().knowledge.retrieve(question, k=3)
        documents = "\n".join(c.text for c in chunks) or "(không có tài liệu liên quan)"
        documents = f"\nTài liệu của xưởng (nguồn duy nhất được dùng):\n{documents}\n"

    open_pending = []
    for item in state.get("pending_interactions", []) or []:
        pending = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if pending.status == InteractionStatus.OPEN:
            open_pending.append(pending)
    offer_question = open_pending[-1].text if open_pending else ""
    context = f"""
{INTENT_CONTEXT[reason].format(question=offer_question)}
{documents}
{profile_text}

Tin nhắn mới của khách: {question}
"""

    reply = generate(CONVERSATION_SYSTEM_PROMPT.format(hotline=settings.hotline, style=STYLE_RULES), context,
                     history=state.get("history"))
    fallback = FALLBACK[reason].format(component=detect_component(question) or "tình trạng xe", question=offer_question)
    update["answers"] = [answer(state, "conversation", reply or fallback)]
    return update
