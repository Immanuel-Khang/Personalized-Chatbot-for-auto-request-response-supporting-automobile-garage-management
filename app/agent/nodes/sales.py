"""Read-only vehicle sales specialist using LLM tool calling.

This agent gives the LLM direct access to product search tools, allowing it to
make intelligent decisions based on conversation context rather than brittle keyword matching.
"""
import json
from typing import Any

from app.agent.llm import generate_with_tools
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, catalog_row, fact_value, known_product, remember_products
from app.contracts.schemas import Product
from app.services import get_services
from app.utils import fmt_vnd



SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe, trả lời tiếng Việt.

QUY TẮC BẮT BUỘC:
Khi khách hỏi về xe (dù tiêu chí mơ hồ như "rẻ", "bền", "tốt"), BẮT BUỘC gọi search_by_natural_language TRƯỚC.
Tool này hiểu ngôn ngữ tự nhiên - KHÔNG BAO GIỜ trả lời trước khi gọi tool.
2. Giá và thông số chỉ lấy từ tool, KHÔNG bịa. Thông số trong "specs" bao gồm:
   - Cơ bản: seats (số chỗ), vehicle_type (loại xe), assembly (lắp ráp), wheel_formula (công thức bánh)
   - Khối lượng: curb_mass_kg (khối lượng bản thân), payload_kg (tải trọng), gvm_kg (tổng khối lượng)
   - Kích thước: wheelbase_mm (chiều dài cơ sở), length/width/height_mm, ground_clearance_mm, turning_radius_m
   - Thùng: cargo_length/width/height_mm (kích thước thùng hàng)
   - Động cơ: engine (mã động cơ), power_kw/power_rpm (công suất), torque_nm (mô-men xoắn), displacement_cc (dung tích),
     emission_standard (tiêu chuẩn khí thải), fuel_type/fuel_tank_l (nhiên liệu)
   - Hộp số: transmission (mã hộp số), gears_forward/gears_reverse (số cấp)
   - Khác: tire_size (lốp), has_abs (phanh ABS)
   Trả thẳng mục khách hỏi; mục không có trong specs thì nói dữ liệu hiện chưa có, nhân viên tư vấn sẽ xác nhận.
   KHÔNG khẳng định xe phù hợp khi dữ liệu không có thông số đó.
3. Mẫu xe MỚI chưa giới thiệu trong lịch sử: giới thiệu ngắn gọn kèm giá niêm yết, ngày áp dụng giá và ưu điểm hợp nhu
   cầu khách. Mẫu ĐÃ giới thiệu: KHÔNG nhắc lại thông số/giá, chỉ trả lời đúng ý mới của khách.
   - Nếu tool trả sales_info: ưu tiên dùng selling_points (điểm mạnh sản phẩm), customer_benefit (lợi ích cho khách),
     primary_use_case (trường hợp sử dụng phù hợp) để giới thiệu xe hợp nhu cầu. KHÔNG nêu hết tất cả, chỉ chọn điểm
     liên quan tới nhu cầu khách.
   - Khách so sánh các mẫu hoặc hỏi xe này khác gì xe khác: dùng advantage_over_competitor (ưu thế cạnh tranh) nếu có.
   - Khách hỏi câu thường gặp về xe (bảo hành, nhiên liệu, bảo trì...): kiểm tra faqs trong sales_info trước khi trả lời.
4. KHÔNG hứa giảm giá, quà tặng hay cam kết. Chỉ tư vấn xe; KHÔNG hỏi ngày giờ hay số điện thoại đặt lịch.
5. Khi chưa rõ khách muốn gì tiếp, hỏi một câu: tìm hiểu thêm về mẫu xe, đặt lịch đến xem trực tiếp hay làm hợp đồng.
6. Khách nói muốn xem bao nhiêu mẫu ("3 xe", "top 5"): truyền đúng số đó vào limit và trả đúng chừng đó mẫu. Khách hỏi
   xe bán chạy/phổ biến/nổi bật: dữ liệu KHÔNG có số liệu bán hàng, nên gọi browse_catalog, nói rõ đây là một số mẫu trong
   danh mục chứ không phải xếp hạng bán chạy, và KHÔNG gọi mẫu nào là bán chạy nhất.
7. browse_catalog trả tên, giá niêm yết và ngày áp dụng giá, KHÔNG có thông số: liệt kê tên kèm giá niêm yết (nói rõ
   giá áp dụng từ ngày nào), KHÔNG nêu thông số. Khi khách yêu cầu thông tin chi tiết/thông số của các xe cụ thể: gọi
   find_by_model_name cho từng xe và nêu thông số của đúng các xe đó.
8. Trả lời gọn, tối đa khoảng 250 token (khoảng 120 từ): chỉ nêu điều khách hỏi, không lặp lại, không rào đón.
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
    _tool("search_by_natural_language", "Tìm xe theo ngôn ngữ tự nhiên bằng AI (RAG). Tool này hiểu cả tiêu chí mơ hồ như "
          "'rẻ', 'bền', 'tốt', 'nhanh', 'tiết kiệm' và các mô tả chi tiết như 'xe giao hàng trong thành phố', "
          "'xe chở hàng nặng đường đèo'. GỌI TOOL NÀY TRƯỚC khi trả lời khách - dữ liệu sales_info chứa thông tin về "
          "độ bền, tiết kiệm, ưu điểm phù hợp với query. Có thể kết hợp với ngân sách (mặc định 3 kết quả).",
          {"query": {"type": "string", "description": "Mô tả nhu cầu của khách - có thể là tiêu chí đơn giản như 'rẻ', 'bền' hoặc chi tiết như 'xe giao hàng nội đô'"},
           "max_budget_vnd": {"type": "number", "description": "Ngân sách tối đa (VNĐ), không bắt buộc"},
           **_LIMIT}, ["query"]),
    _tool("search_products", "Tìm xe theo từ khóa đơn giản ('chassis', 'thùng kín', 'mui bạt'...) và/hoặc ngân sách tối đa. "
          "Dùng khi khách chỉ nêu từ khóa kỹ thuật hoặc chỉ có ngân sách (mặc định 3 kết quả).",
          {"query": {"type": "string", "description": "Từ khóa tìm kiếm; có thể bỏ trống nếu chỉ lọc theo ngân sách"},
           "max_budget_vnd": {"type": "number", "description": "Ngân sách tối đa (VNĐ), VD 500 triệu -> 500000000"},
           **_LIMIT}),
    _tool("find_by_model_name", "Tìm xe theo tên mẫu cụ thể ('FUSO Canter', 'ISUZU Forward'...); dùng cả khi khách hỏi "
          "thông số của một mẫu (mặc định 3 kết quả).",
          {"model_name": {"type": "string", "description": "Tên mẫu xe khách hỏi"}, **_LIMIT}, ["model_name"]),
    _tool("find_by_price_range", "Tìm xe theo khoảng giá hoặc tìm xe đắt/rẻ nhất. Dùng khi khách hỏi 'xe đắt nhất', "
          "'xe rẻ nhất', 'xe trong khoảng X-Y triệu'. Tìm đắt/rẻ nhất: để trống min/max, chọn sort_order (mặc định 5 kết quả).",
          {"min_price_vnd": {"type": "number", "description": "Giá tối thiểu (VNĐ), bỏ trống nếu không giới hạn"},
           "max_price_vnd": {"type": "number", "description": "Giá tối đa (VNĐ), bỏ trống nếu không giới hạn"},
           "sort_order": {"type": "string", "enum": ["asc", "desc"],
                         "description": "asc = rẻ nhất trước, desc = đắt nhất trước (mặc định desc)"},
           **_LIMIT}),
    _tool("browse_catalog", "Danh sách xe trong catalog khi khách muốn xem tất cả / có mẫu nào / xe nổi bật "
          "(mặc định 5 mẫu). Trả TÊN, giá niêm yết và ngày áp dụng giá; cần thông số thì gọi find_by_model_name.", _LIMIT),
]


MAX_RESULTS = 5


def _limit(tool_input: dict[str, Any], default: int) -> int:
    """How many cars the customer asked for (chosen by the LLM), kept within 1..MAX_RESULTS."""
    try:
        return max(1, min(int(tool_input.get("limit") or default), MAX_RESULTS))
    except (TypeError, ValueError):
        return default


def _execute_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Run one product tool; every result is {"products": [...]} plus an optional message for the LLM."""
    import logging
    logger = logging.getLogger(__name__)

    service = get_services().product
    try:
        if tool_name == "search_by_natural_language":
            query = tool_input.get("query", "")
            budget = tool_input.get("max_budget_vnd")
            # Fix: Only use budget if it's a positive number, otherwise None
            budget = budget if budget and budget > 0 else None
            logger.info(f"[TOOL CALLED] search_by_natural_language: query='{query}', budget={budget}")
            products = service.retrieve(query=query, k=_limit(tool_input, 3), max_budget_vnd=budget)
            logger.info(f"[TOOL RESULT] Found {len(products)} products")
            note = None
            if not products:
                note = f"Không tìm thấy xe phù hợp với nhu cầu '{query}'"
                if budget:
                    note += f" trong ngân sách {fmt_vnd(budget)}"
        elif tool_name == "search_products":
            budget = tool_input.get("max_budget_vnd")
            products = service.search(query=tool_input.get("query") or "", limit=_limit(tool_input, 3),
                                      fallback=True, max_budget_vnd=budget)
            note = f"Không có xe trong ngân sách {fmt_vnd(budget)}" if budget is not None and not products else None
        elif tool_name == "find_by_model_name":
            products = service.find_model(name=tool_input["model_name"], limit=_limit(tool_input, 3))
            note = None if products else f"Không tìm thấy mẫu '{tool_input['model_name']}'"
        elif tool_name == "find_by_price_range":
            min_price = tool_input.get("min_price_vnd")
            max_price = tool_input.get("max_price_vnd")
            sort_order = tool_input.get("sort_order", "desc")
            limit = _limit(tool_input, 5)

            # Lấy nhiều xe hơn để filter và sort
            all_products = service.list_vehicles(limit=100)

            # Filter theo khoảng giá
            filtered = all_products
            if min_price is not None:
                filtered = [p for p in filtered if p.list_price_vnd >= min_price]
            if max_price is not None:
                filtered = [p for p in filtered if p.list_price_vnd <= max_price]

            # Sort theo giá
            reverse = (sort_order == "desc")
            products = sorted(filtered, key=lambda p: p.list_price_vnd, reverse=reverse)[:limit]

            note = None
            if not products:
                if min_price and max_price:
                    note = f"Không có xe trong khoảng {fmt_vnd(min_price)} - {fmt_vnd(max_price)}"
                else:
                    note = "Không tìm thấy xe phù hợp"
        elif tool_name == "browse_catalog":
            products = service.list_vehicles(limit=_limit(tool_input, 5))
            note = ", ".join(f"{count} mẫu xe {'tải' if kind == 'TRUCK' else 'con'}"
                             for kind, count in service.vehicle_summary().items())
            return {"products": [catalog_row(p) for p in products], "message": note}  # no specs: lowest latency
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}
    return {"products": [_product_to_dict(p) for p in products], **({"message": note} if note else {})}


def _product_to_dict(product: Product) -> dict:
    """Convert Product to dict for LLM."""
    result = {
        "id": product.id,
        "name": product.name,
        "list_price_vnd": product.list_price_vnd,
        "price_as_of": str(product.price_as_of),
        "specs": product.specs or {},
        "kind": product.kind
    }
    if product.sales_info:
        result["sales_info"] = product.sales_info
    return result


def _viewed_products(state: AgentState, names: list[str]) -> list[dict]:
    """Catalog rows of the cars in car_list (the list the customer counts from). They come from the session's shared
    catalog; only a car no agent has fetched yet (e.g. a list from before the catalog was shared) is read from the DB."""
    service = get_services().product
    rows = []
    for name in names:
        row = known_product(state, name)
        if row is None:
            row = next((catalog_row(p) for p in service.find_model(name, limit=10) if p.name.casefold() == name.casefold()), None)
        if row:
            rows.append({key: row[key] for key in ("name", "list_price_vnd", "price_as_of")})
    return rows


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
    viewed_products = _viewed_products(state, viewed)
    viewed_line = (f"\nXe khách đã xem (có thể chưa liên quan tới tin nhắn này), dữ liệu catalog: "
                   f"{json.dumps(viewed_products, ensure_ascii=False)}\n" if viewed_products else "")

    # Build user message - keep it simple to help LLM focus on tool calling
    facts_text = _format_known_facts(state)
    user_message = state.get('task_text') or state.get('user_text', '')

    context_parts = [f"Tin nhắn của khách: {user_message}"]

    # Add explicit instruction to call tool if user is asking about products
    # Check if query contains product-related keywords
    query_lower = user_message.lower()
    product_keywords = ['xe', 'rẻ', 'bền', 'tốt', 'nhanh', 'tiết kiệm', 'chở', 'tải', 'giao hàng']
    if any(kw in query_lower for kw in product_keywords):
        context_parts.append(f"\n\n[INSTRUCTION] Khách hỏi về xe. GỌI TOOL search_by_natural_language với query=\"{user_message}\" TRƯỚC KHI trả lời.")

    # Only add facts if they exist
    if facts_text != "Chưa có thông tin về nhu cầu khách.":
        context_parts.append(f"\n\nThông tin đã biết về khách:\n{facts_text}")

    # Only add viewed products if they exist
    if viewed_line:
        context_parts.append(viewed_line)

    # Only add note if there are specific cars or purchase interest
    if note:
        context_parts.append(note)

    context = "".join(context_parts)
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

    # Prices the reply may quote: the cars the tools returned plus the cars in car_list (checked by the output guard).
    products = viewed_products + [p for call in result.get("tool_calls", [])
                                  for p in call.get("result", {}).get("products", [])]
    update = {"trace": ["sales"], "answers": [answer(state, "sales", reply)]}
    if priced := [p["list_price_vnd"] for p in products if "list_price_vnd" in p]:
        update["allowed_amounts"] = [fmt_vnd(price) for price in priced]
    # Share what the tools returned: contract (and any later agent) reads the price from state instead of the catalog.
    update["catalog"] = remember_products(state, products)
    return update
