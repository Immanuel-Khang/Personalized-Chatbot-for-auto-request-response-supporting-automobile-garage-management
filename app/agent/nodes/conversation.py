"""[TRACK B] Conversation Agent: xử lý greeting, out-of-scope, cancel, hỏi lại khi chưa rõ ý,
và truy vấn thông tin từ hội thoại. Chỉ đọc, không ghi dữ liệu."""
from app.agent.llm import generate
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending
from app.config import settings
from app.contracts.schemas import Intent
from app.db.models import ChatSession, Customer
from app.db.session import SessionLocal
from app.services import get_services

CONVERSATION_SYSTEM_PROMPT = """\
Bạn là trợ lý thân thiện của xưởng Foton. Nhiệm vụ của bạn:
1. Chào lại ngắn gọn khi khách chào. Nếu chưa biết tên khách, hỏi lịch sự "Bạn có thể cho mình biết họ tên để thuận tiện xưng hô được không ạ?" rồi giới thiệu các dịch vụ.
2. Xưng hô: LUÔN dùng "mình" hoặc "shop" (bot) với "bạn" (khách). KHÔNG dùng "em - anh/chị".
3. Hướng dẫn lịch sự khi khách hỏi ngoài phạm vi (gợi ý: tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử, kiến thức kỹ thuật)
4. Xác nhận khi khách muốn hủy yêu cầu
5. Trả lời câu hỏi về thông tin khách đã cung cấp trước đó (tên, số điện thoại)
6. Trả lời câu hỏi về dịch vụ / thủ tục dựa trên tài liệu được cung cấp
7. Khi chưa hiểu khách muốn gì: hỏi lại để làm rõ, có thể nêu cách bạn đang hiểu để khách xác nhận

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
    "greeting": "Khách đang chào hỏi. {name_prompt}",
    "cancel": ("Khách muốn hủy yêu cầu đang xử lý. Xác nhận ngắn gọn đã dừng đúng việc đó. Nếu cùng tin nhắn khách còn "
               "hỏi việc khác thì phần đó do bộ phận khác trả lời ngay sau: KHÔNG trả lời thay, KHÔNG gợi ý hay hỏi thêm."),
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
    "greeting": ("Dạ chào bạn 👋 Mình là trợ lý của xưởng Foton ạ. "
                 "Mình có thể tư vấn xe, báo giá, đặt lịch bảo dưỡng/lái thử, hoặc giải đáp kỹ thuật.\n"
                 "Bạn có thể cho mình biết họ tên để thuận tiện xưng hô được không ạ?"),
    "cancel": "Dạ, mình đã dừng yêu cầu đang chờ ạ. Bạn cần mình hỗ trợ gì thêm thì cứ nhắn nhé.",
    "clarify": "Dạ, mình chưa hiểu rõ ý bạn lắm ạ. Bạn nói rõ hơn giúp mình được không ạ?",
    "service_inquiry": ("Dạ, về {component} thì kỹ thuật viên bên mình cần xem trực tiếp mới xác nhận được hướng xử lý và chi phí ạ. "
                        "Nếu bạn muốn, mình có thể sắp xếp để bạn mang xe qua xưởng kiểm tra."),
    "contract_inquiry": ("Dạ, danh mục giấy tờ cụ thể sẽ do nhân viên tư vấn xác nhận theo hình thức mua của bạn ạ. "
                         "Bạn cần mình chuyển câu hỏi này cho nhân viên tư vấn không?"),
    "decline": "Dạ vâng ạ. Bạn cần mình hỗ trợ gì thêm thì cứ nhắn nhé.",
    "clarify_offer": "{question}",
    "other": "Dạ, mình chưa hiểu rõ ý bạn ạ. Bạn có thể hỏi về giá xe, đặt lịch bảo dưỡng/lái thử, hoặc kiến thức kỹ thuật nhé.",
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
    update: dict = {"trace": ["conversation"]}
    question = state.get("task_text") or state.get("user_text", "")
    reason = _reason(state)

    # Lấy thông tin khách từ DB (lịch sử hội thoại đi dạng message qua state['history'])
    session_id = state.get("session_id", "")
    name, phone = _get_customer_profile(session_id)

    if name or phone:
        profile_text = f"Thông tin khách đã lưu: Tên: {name or 'chưa có'}, SĐT: {phone or 'chưa có'}"
    else:
        profile_text = "Chưa có thông tin khách hàng trong hồ sơ."

    # For greeting, add name request instruction if name is unknown
    name_prompt = ""
    if reason == "greeting" and not name:
        name_prompt = ("Sau khi chào và giới thiệu dịch vụ, hỏi lịch sự: 'Bạn có thể cho mình biết họ tên để "
                      "thuận tiện xưng hô được không ạ?'")
    elif reason == "greeting" and name:
        name_prompt = f"Đã biết tên khách là {name}."

    documents = ""
    if reason in ("service_inquiry", "contract_inquiry"):
        chunks = get_services().knowledge.retrieve(question, k=3)
        documents = "\n".join(c.text for c in chunks) or "(không có tài liệu liên quan)"
        documents = f"\nTài liệu của xưởng (nguồn duy nhất được dùng):\n{documents}\n"

    pending = open_pending(state)
    offer_question = pending[-1].text if pending else ""
    context = f"""
{INTENT_CONTEXT.get(reason, INTENT_CONTEXT["other"]).format(question=offer_question, name_prompt=name_prompt)}
{documents}
{profile_text}

Tin nhắn mới của khách: {question}
"""

    reply = generate(CONVERSATION_SYSTEM_PROMPT.format(hotline=settings.hotline, style=STYLE_RULES), context,
                     history=state.get("history"))
    detail = fact_value(state.get("known_facts"), "service_detail")
    fallback = FALLBACK.get(reason, FALLBACK["other"]).format(component=detail or "tình trạng xe",
                                                                question=offer_question)
    update["answers"] = [answer(state, "conversation", reply or fallback)]
    return update
