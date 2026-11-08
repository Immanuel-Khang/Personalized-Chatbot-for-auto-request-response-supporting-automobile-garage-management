"""Read-only vehicle sales specialist using LLM tool calling.

This agent gives the LLM direct access to product search tools, allowing it to
make intelligent decisions based on conversation context rather than brittle keyword matching.
"""
from typing import Any

from app.agent.llm import generate_with_tools
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value
from app.contracts.schemas import Product
from app.services import get_services
from app.utils import fmt_vnd



SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe của xưởng Foton, thân thiện, trả lời tiếng Việt.
1. Khách hỏi về xe, nêu tiêu chí (tải trọng, ngân sách, loại thùng...) hay tên xe: gọi tool tìm trước rồi tư vấn dựa
   trên kết quả tool; không rõ tool nào thì search_products với mô tả nhu cầu. Chỉ dùng thông tin khách đã cung cấp.
2. Giá và thông số chỉ lấy từ tool, KHÔNG bịa. Thông số chỉ nêu mục có trong "specs", trả thẳng mục khách hỏi; mục
   không có (kích thước thùng, tải trọng chi tiết...) thì nói dữ liệu hiện chưa có, nhân viên tư vấn sẽ xác nhận.
   KHÔNG khẳng định xe phù hợp khi dữ liệu không có thông số đó.
3. Mẫu xe MỚI chưa giới thiệu trong lịch sử: giới thiệu ngắn gọn kèm giá niêm yết, ngày áp dụng giá và ưu điểm hợp nhu
   cầu khách. Mẫu ĐÃ giới thiệu: KHÔNG nhắc lại thông số/giá, chỉ trả lời đúng ý mới của khách.
4. KHÔNG hứa giảm giá, quà tặng hay cam kết. Chỉ tư vấn xe; KHÔNG hỏi ngày giờ hay số điện thoại đặt lịch.
5. Khi chưa rõ khách muốn gì tiếp, hỏi một câu: tìm hiểu thêm về mẫu xe, đặt lịch đến xem trực tiếp hay làm hợp đồng.
6. Khách nói muốn xem bao nhiêu mẫu ("3 xe", "top 5"): truyền đúng số đó vào limit và trả đúng chừng đó mẫu. Khách hỏi
   xe bán chạy/phổ biến/nổi bật: dữ liệu KHÔNG có số liệu bán hàng, nên gọi browse_catalog, nói rõ đây là một số mẫu trong
   danh mục chứ không phải xếp hạng bán chạy, và KHÔNG gọi mẫu nào là bán chạy nhất.
""" + STYLE_RULES


PURCHASE_INTEREST_NOTE = """
Khách vừa bày tỏ muốn mua xe nhưng CHƯA yêu cầu làm hợp đồng. Trả lời 2-3 câu, hỏi MỘT câu cho bước tiếp theo:
- Khách đã nêu/chỉ tới một xe cụ thể (ở trên): ghi nhận xe đó, hỏi khách muốn (1) tìm hiểu thêm về xe (thông số, giá, so
  sánh) hay (2) đặt lịch đến showroom xem xe và ký hợp đồng.
- Khách CHƯA nêu xe nào: KHÔNG tự chọn xe. Nếu có "Xe khách đã xem" thì nêu tên các xe đó và hỏi khách muốn mua một
  trong số đó hay muốn tìm thêm lựa chọn khác; nếu chưa có thì hỏi khách muốn mua dòng xe nào / nhu cầu gì.
KHÔNG xin tên, SĐT, địa chỉ, giá hay ngày giờ; KHÔNG liệt kê lại thông số; KHÔNG tự làm hợp đồng.
"""


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or []}}


_LIMIT = {"limit": {"type": "integer", "description": "Số kết quả tối đa"}}
PRODUCT_SEARCH_TOOLS = [
    _tool("search_products", "Tìm xe theo mô tả nhu cầu ('chassis', 'thùng kín', 'mui bạt'...) và/hoặc ngân sách tối đa. "
          "Có ngân sách thì truyền max_budget_vnd; kết quả không bao giờ vượt ngân sách (mặc định 3 kết quả).",
          {"query": {"type": "string", "description": "Nhu cầu của khách; có thể bỏ trống nếu chỉ lọc theo ngân sách"},
           "max_budget_vnd": {"type": "number", "description": "Ngân sách tối đa (VNĐ), VD 500 triệu -> 500000000"},
           **_LIMIT}),
    _tool("find_by_model_name", "Tìm xe theo tên mẫu cụ thể ('FUSO Canter', 'ISUZU Forward'...); dùng cả khi khách hỏi "
          "thông số của một mẫu (mặc định 3 kết quả).",
          {"model_name": {"type": "string", "description": "Tên mẫu xe khách hỏi"}, **_LIMIT}, ["model_name"]),
    _tool("browse_catalog", "Danh sách xe trong catalog khi khách muốn xem tất cả / có mẫu nào / xe nổi bật "
          "(mặc định 10 mẫu).", _LIMIT),
]


MAX_RESULTS = 20


def _limit(tool_input: dict[str, Any], default: int) -> int:
    """How many cars the customer asked for (chosen by the LLM), kept within 1..MAX_RESULTS."""
    try:
        return max(1, min(int(tool_input.get("limit") or default), MAX_RESULTS))
    except (TypeError, ValueError):
        return default


def _execute_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Run one product tool; every result is {"products": [...]} plus an optional message for the LLM."""
    service = get_services().product
    try:
        if tool_name == "search_products":
            budget = tool_input.get("max_budget_vnd")
            products = service.search(query=tool_input.get("query") or "", limit=_limit(tool_input, 3),
                                      fallback=True, max_budget_vnd=budget)
            note = f"Không có xe trong ngân sách {fmt_vnd(budget)}" if budget is not None and not products else None
        elif tool_name == "find_by_model_name":
            products = service.find_model(name=tool_input["model_name"], limit=_limit(tool_input, 3))
            note = None if products else f"Không tìm thấy mẫu '{tool_input['model_name']}'"
        elif tool_name == "browse_catalog":
            products = service.list_vehicles(limit=_limit(tool_input, 10))
            note = ", ".join(f"{count} mẫu xe {'tải' if kind == 'TRUCK' else 'con'}"
                             for kind, count in service.vehicle_summary().items())
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}
    return {"products": [_product_to_dict(p) for p in products], **({"message": note} if note else {})}


def _product_to_dict(product: Product) -> dict:
    """Convert Product to dict for LLM."""
    return {
        "id": product.id,
        "name": product.name,
        "list_price_vnd": product.list_price_vnd,
        "price_as_of": str(product.price_as_of),
        "specs": product.specs or {},
        "kind": product.kind
    }


# Contact, booking and deal data stay out; a car chosen for an earlier deal is not a fact about this request.
ADVICE_FACTS = ("budget_vnd", "intended_use", "payload_tons")


def _format_known_facts(state: AgentState) -> str:
    facts = state.get("known_facts") or {}
    lines = [f"- {key}: {fact_value(facts, key)}" for key in ADVICE_FACTS if fact_value(facts, key) is not None]
    return "\n".join(lines) or "Chưa có thông tin về nhu cầu khách."


def sales(state: AgentState) -> dict:
    """Sales agent that uses LLM tool calling to search products."""
    # The cars the customer pointed at were resolved by preprocessing from the list they count ("xe số 2").
    interpretation = state.get("conversation_interpretation")
    task_ids = set(state.get("task_ids") or [])
    tasks = [task for task in (interpretation.tasks if interpretation else []) if task.task_id in task_ids]
    cars = list(dict.fromkeys(task.car_model for task in tasks if task.car_model))
    named = ", ".join(f'"{car}"' for car in cars)
    note = (f"\nKhách đang nói về: {named}. Gọi find_by_model_name với đúng tên đó cho từng mẫu và chỉ trả lời về "
            "các mẫu này, không liệt kê lại danh sách.\n" if cars else "")
    if any(task.action == "purchase_interest" for task in tasks):
        note += ("\nTin nhắn này CHƯA chỉ tới xe nào (xe khách đã xem ở trên chỉ là bối cảnh, không phải xe khách chọn)."
                 if not cars else "") + "\n" + PURCHASE_INTEREST_NOTE

    viewed = fact_value(state.get("known_facts"), "car_list") or []
    viewed_line = f"\nXe khách đã xem (có thể chưa liên quan tới tin nhắn này): {', '.join(viewed)}\n" if viewed else ""
    context = (f"Tin nhắn của khách: {state.get('task_text') or state.get('user_text', '')}\n\n"
               f"Thông tin đã biết về khách:\n{_format_known_facts(state)}\n{viewed_line}{note}")
    result = generate_with_tools(
        system_prompt=SALES_SYSTEM_PROMPT,
        user_message=context,
        tools=PRODUCT_SEARCH_TOOLS,
        tool_executor=_execute_tool,
        history=state.get("history"),
        max_turns=3,
    )
    if result.get("error"):
        # Internal errors never reach the customer.
        reply = ("Xin lỗi, mình chưa tra cứu được thông tin xe lúc này. Bạn cho mình biết nhu cầu (tải trọng, "
                 "loại thùng, ngân sách) để mình tư vấn nhé.")
    else:
        reply = result.get("final_response", "Xin lỗi, mình chưa thể tư vấn được ạ.")

    # Prices the reply may quote: exactly those the tools returned (checked by the output guard).
    products = [p for call in result.get("tool_calls", []) for p in call.get("result", {}).get("products", [])]
    update = {"trace": ["sales"], "answers": [answer(state, "sales", reply)]}
    if products:
        update["allowed_amounts"] = [fmt_vnd(p["list_price_vnd"]) for p in products]
    return update
