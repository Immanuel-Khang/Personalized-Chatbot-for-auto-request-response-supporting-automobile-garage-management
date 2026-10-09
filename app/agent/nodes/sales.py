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
SUGGESTION_KEYWORDS = ["phổ biến", "bán chạy", "gợi ý", "nên mua", "nào tốt", "xe gì", "dòng xe nào", "loại xe nào"]

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
    text = state.get("user_text", "").lower()

    # Mặc cả xuất hiện giữa luồng → chuyển người
    if any(k in text for k in NEGOTIATION):
        return {"needs_human": True, "handover_reason": "NEGOTIATION", "negotiation_active": True,
                "trace": step(state, "sales")}

    car_model = slots.get("car_model")
    budget = slots.get("budget_vnd")
    is_suggestion = any(k in text for k in SUGGESTION_KEYWORDS)

    # Chọn phương thức tìm kiếm phù hợp
    svc = get_services().product
    if car_model:
        # Ưu tiên tìm đúng xe khách đề cập
        exact = svc.get_by_name(car_model)
        products = ([exact] if exact else []) + svc.search(car_model, limit=3)
        # Loại bỏ trùng lặp
        seen, deduped = set(), []
        for p in products:
            if p.id not in seen:
                seen.add(p.id)
                deduped.append(p)
        products = deduped[:3]
    elif budget:
        products = svc.search_by_budget(budget, limit=3)
    elif is_suggestion:
        products = svc.get_popular(limit=3)
    else:
        products = svc.search(state.get("user_text", ""), limit=3)

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

    llm_reply = generate(
        SALES_SYSTEM_PROMPT,
        f"Câu hỏi của khách: {state.get('user_text', '')}\n\n"
        f"Dữ liệu xe từ hệ thống:\n{product_info}\n\n"
        f"Thông tin đã biết về khách: {slots}\n"
        f"Hãy tư vấn dựa trên dữ liệu trên."
    )
    reply = llm_reply or ("Mình gửi bạn một vài lựa chọn:\n" + product_info +
                           "\nBạn muốn mình tư vấn kỹ hơn hay đặt lịch lái thử không?")

    update = {"draft_reply": reply, "allowed_amounts": amounts, "trace": step(state, "sales")}
    if any(k in text for k in TEST_DRIVE):
        update["handoff_to"] = "appointment"
        update["slots"] = {**slots, "service_type": ServiceType.TEST_DRIVE.value}
    return update
