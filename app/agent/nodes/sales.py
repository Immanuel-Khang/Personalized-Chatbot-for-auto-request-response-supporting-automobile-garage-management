"""[TRACK B] Sales Agent: tra cứu giá từ DB, dùng LLM sinh câu trả lời tự nhiên.
Chỉ đọc, không có quyền về giá: chỉ báo giá niêm yết kèm ngày hiệu lực.
Mặc cả giữa luồng -> chuyển người; muốn lái thử -> chuyển sang appointment."""
from app.agent.llm import generate
from app.agent.nodes.common import step
from app.agent.nodes.preprocess import KEYWORDS
from app.agent.state import AgentState
from app.contracts.schemas import Intent, ServiceType
from app.services import get_services
from app.utils import fmt_vnd

NEGOTIATION = KEYWORDS[Intent.DISCOUNT]
TEST_DRIVE = ["lái thử", "chạy thử"]

SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe Toyota chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Khi nêu giá, PHẢI dùng đúng số liệu được cung cấp, KHÔNG ĐƯỢC tự bịa giá.
3. Kèm ngày hiệu lực giá (price_as_of) khi báo giá.
4. KHÔNG hứa giảm giá, quà tặng hay cam kết nào.
5. Gợi ý đặt lịch lái thử hoặc hỏi thêm nhu cầu.
6. Sử dụng thông tin về khách hàng (slots) để tư vấn cá nhân hóa, KHÔNG hỏi lại thông tin đã có.
"""


def sales(state: AgentState) -> dict:
    slots = state.get("slots", {})
    text = state["user_text"].lower()

    # Mặc cả xuất hiện giữa luồng (preprocess phân loại SALES nhưng câu có ý mặc cả) → chuyển người
    if any(k in text for k in NEGOTIATION):
        return {"needs_human": True, "handover_reason": "NEGOTIATION", "negotiation_active": True,
                "trace": step(state, "sales")}

    # Tra cứu sản phẩm từ DB qua service interface
    products = get_services().product.search(state["user_text"], limit=3)

    # Nếu khách hỏi cụ thể 1 xe → tìm chính xác
    car_model = slots.get("car_model")
    if car_model:
        exact = get_services().product.get_by_name(car_model)
        if exact and exact not in products:
            products = [exact] + products[:2]

    if not products:
        return {"draft_reply": "Hiện mình chưa tìm thấy sản phẩm phù hợp. Bạn cho mình biết thêm nhu cầu nhé?",
                "trace": step(state, "sales")}

    lines, amounts = [], []
    for p in products:
        price = fmt_vnd(p.list_price_vnd)
        amounts.append(price)
        specs_str = ", ".join(f"{k}: {v}" for k, v in p.specs.items()) if p.specs else ""
        lines.append(f"- {p.name}: giá niêm yết {price} (cập nhật {p.price_as_of})" +
                      (f" | {specs_str}" if specs_str else ""))

    product_info = "\n".join(lines)

    # Dùng LLM sinh câu trả lời tự nhiên
    llm_reply = generate(
        SALES_SYSTEM_PROMPT,
        f"Câu hỏi của khách: {state['user_text']}\n\n"
        f"Dữ liệu xe từ hệ thống:\n{product_info}\n\n"
        f"Thông tin đã biết về khách: {slots}\n"
        f"Hãy tư vấn dựa trên dữ liệu trên."
    )
    if llm_reply:
        reply = llm_reply
    else:
        # Fallback nếu không có LLM
        reply = "Mình gửi bạn một vài lựa chọn:\n" + product_info + \
                "\nBạn muốn mình tư vấn kỹ hơn hay đặt lịch lái thử không?"

    update = {"draft_reply": reply, "allowed_amounts": amounts, "trace": step(state, "sales")}
    if any(k in text for k in TEST_DRIVE):  # muốn lái thử → appointment hỏi tiếp slot
        update["handoff_to"] = "appointment"
        update["slots"] = {**slots, "service_type": ServiceType.TEST_DRIVE.value}
    return update
