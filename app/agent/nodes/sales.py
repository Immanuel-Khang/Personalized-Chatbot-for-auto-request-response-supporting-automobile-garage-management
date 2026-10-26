"""[TRACK B] Sales Agent: tra cứu giá từ DB, dùng LLM sinh câu trả lời tự nhiên.
Chỉ đọc, không có quyền về giá: chỉ báo giá niêm yết kèm ngày hiệu lực.
Mặc cả do Policy Guard xử lý; chỉ đọc dữ liệu, chạy song song với các agent khác."""
from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.state import AgentState
from app.services import get_services
from app.utils import fmt_vnd

SUGGESTION_KEYWORDS = ["phổ biến", "bán chạy", "gợi ý", "nên mua", "nào tốt", "xe gì", "dòng xe nào", "loại xe nào"]

SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe của xưởng Foton chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Khi nêu giá, PHẢI dùng đúng số liệu được cung cấp, KHÔNG ĐƯỢC tự bịa giá.
3. Kèm ngày hiệu lực giá (price_as_of) khi báo giá.
4. KHÔNG hứa giảm giá, quà tặng hay cam kết nào.
5. Chỉ trả lời phần câu hỏi được giao; KHÔNG tự hỏi ngày giờ hay số điện thoại đặt lịch.
6. Sử dụng thông tin về khách hàng (slots) để tư vấn cá nhân hóa, KHÔNG hỏi lại thông tin đã có.
""" + STYLE_RULES


def sales(state: AgentState) -> dict:
    slots = state.get("slots", {})
    question = state.get("task_text") or state.get("user_text", "")
    text = question.lower()

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
        products = svc.search(question, limit=3)

    if not products:
        reply = "Hiện mình chưa tìm thấy sản phẩm phù hợp. Bạn cho mình biết thêm nhu cầu nhé?"
        return {"answers": [answer(state, "sales", reply)], "trace": step(state, "sales")}

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
        f"Câu hỏi của khách: {question}\n\n"
        f"Dữ liệu xe từ hệ thống:\n{product_info}\n\n"
        f"Thông tin đã biết về khách: {slots}\n"
        f"Hãy tư vấn dựa trên dữ liệu trên."
    )
    reply = llm_reply or ("Mình gửi bạn một vài lựa chọn:\n" + product_info +
                           "\nBạn muốn mình tư vấn kỹ hơn hay đặt lịch lái thử không?")

    # Không tự gợi ý đặt lịch: nếu khách muốn lái thử, appointment chạy cùng lượt và hỏi slot ở cuối
    return {"answers": [answer(state, "sales", reply)], "allowed_amounts": amounts, "trace": step(state, "sales")}
