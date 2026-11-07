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
Bạn là trợ lý tư vấn xe của xưởng Foton chuyên nghiệp, thân thiện.

NGUYÊN TẮC HOẠT ĐỘNG:
1. Phân tích nhu cầu khách hàng từ lịch sử hội thoại và tin nhắn hiện tại
2. Sử dụng tools để tìm kiếm sản phẩm phù hợp
3. Tư vấn dựa trên kết quả tìm kiếm thực tế từ tools
4. Luôn trả lời bằng tiếng Việt

QUY TẮC BẮT BUỘC:
1. Khi nêu giá, PHẢI dùng đúng số liệu từ tool, KHÔNG bịa giá
2. Chỉ báo giá chi tiết khi khách hỏi muốn xem giá chi tiết kèm theo "giá có hiệu lực từ ngày...". 
   Còn khi giới thiệu chỉ đưa khoảng giá ví dụ: "Tầm tài chính của xe này trong khoảng..." và so sánh chung nhất giữa các mẫu xe được đưa ra
3. KHÔNG hứa giảm giá, quà tặng hay cam kết nào
4. Chỉ tư vấn xe; KHÔNG hỏi về ngày giờ hay số điện thoại đặt lịch
5. Chỉ dùng thông tin khách được cung cấp trong dữ liệu đã biết
6. KHÔNG khẳng định xe phù hợp khi dữ liệu xe không có thông số đó

KHI NÀO GỌI TOOL:
- Khách hỏi về xe, cần tư vấn, muốn xem sản phẩm → Gọi tool tìm kiếm ngay
- Khách cung cấp tiêu chí (tải trọng, ngân sách, loại thùng...) → Gọi tool với tiêu chí đó
- Khách hỏi xe phổ biến/nổi bật → Gọi get_popular_products
- Khách muốn xem danh sách → Gọi browse_catalog
- Khách nói tên xe cụ thể → Gọi find_by_model_name
- Khách có ngân sách → Gọi search_products với max_budget_vnd (kèm query nếu có thêm tiêu chí)
- Khách hỏi thông số (động cơ, hộp số, số chỗ, nhiên liệu) của một mẫu xe → find_by_model_name hoặc
  get_product_details nếu đã biết ID, rồi trả lời đúng thông số trong "specs"
- Không rõ tool nào → Gọi search_products với mô tả nhu cầu

THÔNG SỐ XE:
- Chỉ nêu thông số có trong "specs" của kết quả tool; trả lời thẳng thông số khách hỏi, không liệt kê lại cả xe
- Thông số khách hỏi không có trong specs (kích thước thùng, tải trọng chi tiết...) → nói thật là dữ liệu
  hiện chưa có, nhân viên tư vấn sẽ xác nhận

SAU KHI CÓ KẾT QUẢ:
- Nếu mẫu xe MỚI chưa từng giới thiệu trong lịch sử chat: Giới thiệu ngắn gọn các mẫu tìm được kèm giá niêm yết, ngày áp dụng giá và ưu điểm phù hợp.
- Nếu mẫu xe ĐÃ ĐƯỢC GIỚI THIỆU ở các tin nhắn trước: TUYỆT ĐỐI KHÔNG giới thiệu hay liệt kê lại thông số kỹ thuật, giá niêm yết, động cơ, hộp số... từ đầu. Chỉ trả lời trực tiếp câu hỏi hoặc phân tích điểm khác biệt cho chi tiết mới mà khách vừa cung cấp.
- Làm rõ ưu điểm phù hợp với nhu cầu khách
- Hỏi thêm thông tin về xe nếu khách muốn biết thêm chi tiết về mẫu xe
- Khi không biết khách hàng muốn dịch vụ nào tiếp theo đặt một câu hỏi như: "Bạn muốn tìm hiểu tiếp về mẫu xe, đặt lịch đến xem trực tiếp hoặc làm hợp đồng để mình có thể hỗ trợ ?"
""" + STYLE_RULES


PURCHASE_INTEREST_NOTE = """
Khách vừa bày tỏ muốn mua xe nhưng CHƯA yêu cầu làm hợp đồng. Trả lời ngắn (2-3 câu): ghi nhận tên mẫu xe khách đang quan
tâm (nếu đã biết), rồi hỏi MỘT câu xem khách muốn gì tiếp theo, nêu rõ hai hướng: (1) tìm hiểu thêm về xe (thông số, giá,
so sánh) hoặc (2) đặt lịch đến showroom để xem xe và ký hợp đồng. KHÔNG xin tên, SĐT, địa chỉ, giá hay ngày giờ; KHÔNG
liệt kê lại thông số xe; KHÔNG tự bắt đầu làm hợp đồng.
"""

PRODUCT_SEARCH_TOOLS = [
    {
        "name": "search_products",
        "description": ("Tìm xe theo tiêu chí: mô tả nhu cầu ('chassis', 'thùng kín', 'mui bạt'...) và/hoặc ngân sách "
                        "tối đa. Có ngân sách thì truyền max_budget_vnd; kết quả không bao giờ vượt ngân sách."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Mô tả nhu cầu của khách (có thể bỏ trống nếu chỉ lọc theo ngân sách)"
                },
                "max_budget_vnd": {
                    "type": "number",
                    "description": "Ngân sách tối đa của khách (VNĐ), VD 500 triệu -> 500000000"
                },
                "limit": {
                    "type": "integer",
                    "description": "Số lượng kết quả tối đa (mặc định 3)",
                    "default": 3
                }
            },
            "required": []
        }
    },
    {
        "name": "find_by_model_name",
        "description": "Tìm xe theo tên mẫu cụ thể. Dùng khi khách nói tên mẫu như 'FUSO Canter', 'ISUZU Forward'...",
        "input_schema": {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Tên mẫu xe khách hỏi"
                },
                "limit": {
                    "type": "integer",
                    "description": "Số lượng kết quả tối đa (mặc định 3)",
                    "default": 3
                }
            },
            "required": ["model_name"]
        }
    },
    {
        "name": "get_popular_products",
        "description": "Lấy các mẫu xe phổ biến, nổi bật. Dùng khi khách hỏi 'xe nào phổ biến', 'mẫu nổi bật', 'xe bán chạy'...",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Số lượng mẫu (mặc định 3)",
                    "default": 3
                }
            },
            "required": []
        }
    },
    {
        "name": "browse_catalog",
        "description": "Xem danh sách xe trong catalog. Dùng khi khách muốn 'xem tất cả', 'có mẫu nào', 'danh sách xe'...",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Số lượng hiển thị (mặc định 10)",
                    "default": 10
                }
            },
            "required": []
        }
    },
    {
        "name": "get_product_details",
        "description": "Lấy giá và thông số (specs) của 1 mẫu xe theo ID. Dùng khi khách hỏi về mẫu đã được giới thiệu",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "ID của sản phẩm"
                }
            },
            "required": ["product_id"]
        }
    }
]


def _execute_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a product search tool and return results."""
    service = get_services().product

    try:
        if tool_name == "search_products":
            budget = tool_input.get("max_budget_vnd")
            products = service.search(
                query=tool_input.get("query") or "",
                limit=tool_input.get("limit", 3),
                fallback=True,
                max_budget_vnd=budget,
            )
            if not products and budget is not None:
                return {"products": [], "message": f"Không có xe trong ngân sách {fmt_vnd(budget)}"}
            return {"products": [_product_to_dict(p) for p in products]}

        elif tool_name == "find_by_model_name":
            products = service.find_model(
                name=tool_input["model_name"],
                limit=tool_input.get("limit", 3)
            )
            if not products:
                return {"products": [], "message": f"Không tìm thấy mẫu '{tool_input['model_name']}'"}
            return {"products": [_product_to_dict(p) for p in products]}

        elif tool_name == "get_popular_products":
            products = service.get_popular(limit=tool_input.get("limit", 3))
            return {"products": [_product_to_dict(p) for p in products]}

        elif tool_name == "browse_catalog":
            products = service.list_vehicles(limit=tool_input.get("limit", 10))
            summary = service.vehicle_summary()
            scope = ", ".join(f"{count} mẫu xe {'tải' if kind == 'TRUCK' else 'con'}"
                            for kind, count in summary.items())
            return {
                "products": [_product_to_dict(p) for p in products],
                "catalog_summary": scope
            }

        elif tool_name == "get_product_details":
            product = service.get_by_id(tool_input["product_id"])
            if not product:
                return {"error": f"Không tìm thấy sản phẩm ID {tool_input['product_id']}"}
            return {"product": _product_to_dict(product)}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {"error": str(e)}


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


def _format_known_facts(state: AgentState) -> str:
    """Format known facts about customer for LLM context."""
    facts = state.get("known_facts", {}) or {}
    if not facts:
        return "Chưa có thông tin về nhu cầu khách."

    lines = [f"- {key}: {fact_value(facts, key)}" for key in facts if fact_value(facts, key) is not None]

    return "\n".join(lines) if lines else "Chưa có thông tin về nhu cầu khách."


def sales(state: AgentState) -> dict:
    """Sales agent that uses LLM tool calling to search products."""
    user_message = state.get("task_text") or state.get("user_text", "")
    known_facts_text = _format_known_facts(state)
    # The car the customer pointed at, resolved by preprocessing from the list they count ("xe số 2").
    interpretation = state.get("conversation_interpretation")
    task_ids = set(state.get("task_ids") or [])
    car_models = [task.car_model for task in (interpretation.tasks if interpretation else [])
                  if task.task_id in task_ids and task.car_model]
    if len(set(car_models)) == 1:
        car = car_models[0]
        referenced = (f"\nKhách đang nói về mẫu xe \"{car}\". Gọi find_by_model_name với model_name=\"{car}\" để lấy "
                      f"thông tin của đúng mẫu này và chỉ trả lời về xe đó, không liệt kê lại danh sách.\n")
    elif len(set(car_models)) > 1:
        referenced = ("\nKhách nói về nhiều mẫu xe: " + ", ".join(f'"{m}"' for m in dict.fromkeys(car_models))
                      + ". Trả lời về từng mẫu, không liệt kê lại danh sách.\n")
    else:
        referenced = ""

    purchase_interest = any(task.action == "purchase_interest" for task in (interpretation.tasks if interpretation else [])
                            if task.task_id in task_ids)
    if purchase_interest:
        referenced += PURCHASE_INTEREST_NOTE

    context = f"""Tin nhắn của khách: {user_message}

Thông tin đã biết về khách:
{known_facts_text}
{referenced}
Hãy phân tích nhu cầu và gọi tool phù hợp nếu cần tìm sản phẩm, sau đó tư vấn khách dựa trên kết quả.
LƯU Ý: Nếu khách đang tiếp tục trao đổi về các mẫu xe đã giới thiệu ở các lượt trước, KHÔNG giới thiệu lại toàn bộ thông số hay so sánh từ đầu mà đi thẳng vào ý mới."""

    # Call LLM with tools
    result = generate_with_tools(
        system_prompt=SALES_SYSTEM_PROMPT,
        user_message=context,
        tools=PRODUCT_SEARCH_TOOLS,
        tool_executor=_execute_tool,
        history=state.get("history"),
        max_turns=3  # Allow up to 3 tool calls
    )

    if result.get("error"):
        # Internal errors never reach the customer.
        reply = ("Xin lỗi, mình chưa tra cứu được thông tin xe lúc này. Bạn cho mình biết nhu cầu (tải trọng, "
                 "loại thùng, ngân sách) để mình tư vấn nhé.")
    else:
        reply = result.get("final_response", "Xin lỗi, mình chưa thể tư vấn được ạ.")

    # Extract products that were found
    products_found = []
    for tool_call in result.get("tool_calls", []):
        tool_result = tool_call.get("result", {})
        if "products" in tool_result:
            for p_dict in tool_result["products"]:
                products_found.append(p_dict)
        elif "product" in tool_result:  # get_product_details: the customer picked this one ("mẫu thứ 2")
            products_found.append(tool_result["product"])

    update = {
        "trace": ["sales"],
        "answers": [answer(state, "sales", reply)]
    }

    # The cars the bot offered, so the customer can count them next turn ("xe số 2"). Preprocessing then
    # narrows this to the cars the bot's reply actually presented.
    if products_found:
        found = list({p["id"]: p for p in products_found}.values())
        update["allowed_amounts"] = [fmt_vnd(p_dict["list_price_vnd"]) for p_dict in products_found]

    return update

