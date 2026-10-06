"""[TRACK B] Sales Agent: tra cứu giá từ DB, dùng LLM sinh câu trả lời tự nhiên.
Chỉ báo giá niêm yết kèm ngày hiệu lực. Nếu discount auto-approved thì hiển thị giá đã giảm."""
from app.agent.llm import generate
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.services import get_services
from app.utils import fmt_vnd


SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe Toyota chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Khi nêu giá, PHẢI dùng đúng số liệu được cung cấp, KHÔNG ĐƯỢC tự bịa giá.
3. Kèm ngày hiệu lực giá (price_as_of) khi báo giá.
4. Nếu khách đã được giảm giá, thông báo mức giảm và giá mới.
5. Gợi ý đặt lịch lái thử hoặc hỏi thêm nhu cầu.
6. Sử dụng thông tin về khách hàng (slots) để tư vấn cá nhân hóa, KHÔNG hỏi lại thông tin đã có.
"""


def sales(state: AgentState) -> dict:
    slots = state.get("slots", {})

    # Nếu discount vừa được auto-approve → trả lời xác nhận giảm giá
    if slots.get("discount_result_status") == "AUTO_APPROVED":
        discount_msg = slots.get("discount_result_message", "")
        discounted_price = slots.get("discounted_price", 0)
        car_model = slots.get("car_model", "xe")
        amounts = [fmt_vnd(int(discounted_price))] if discounted_price else []

        llm_reply = generate(
            SALES_SYSTEM_PROMPT,
            f"Khách đã được giảm giá cho xe {car_model}. {discount_msg}\n"
            f"Hãy xác nhận mức giảm giá đã được áp dụng và hỏi khách có muốn chốt mua không.\n"
            f"Thông tin khách: {slots}"
        )
        reply = llm_reply or f"✅ {discount_msg}\nBạn có muốn tiến hành chốt mua không?"

        # Xóa discount result khỏi slots sau khi xử lý
        clean_slots = {k: v for k, v in slots.items() if not k.startswith("discount_result")}
        return {"draft_reply": reply, "allowed_amounts": amounts, "slots": clean_slots,
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

    return {"draft_reply": reply, "allowed_amounts": amounts, "trace": step(state, "sales")}
