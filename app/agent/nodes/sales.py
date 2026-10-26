"""Read-only vehicle sales specialist using LLM tool calling.

This agent gives the LLM direct access to product search tools, allowing it to
make intelligent decisions based on conversation context rather than brittle keyword matching.
"""
from typing import Any

from app.agent.llm import generate_with_tools
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.state import AgentState
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
2. Kèm ngày hiệu lực giá (price_as_of) khi báo giá
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
- Khách có ngân sách cụ thể → Gọi search_by_budget
- Không rõ tool nào → Gọi search_products với mô tả nhu cầu

SAU KHI CÓ KẾT QUẢ:
- Nếu mẫu xe MỚI chưa từng giới thiệu trong lịch sử chat: Giới thiệu ngắn gọn các mẫu tìm được kèm giá niêm yết (price_as_of) và ưu điểm phù hợp.
- Nếu mẫu xe ĐÃ ĐƯỢC GIỚI THIỆU ở các tin nhắn trước: TUYỆT ĐỐI KHÔNG giới thiệu hay liệt kê lại thông số kỹ thuật, giá niêm yết, động cơ, hộp số... từ đầu. Chỉ trả lời trực tiếp câu hỏi hoặc phân tích điểm khác biệt cho chi tiết mới mà khách vừa cung cấp.
- Làm rõ ưu điểm phù hợp với nhu cầu khách
- Hỏi thêm thông tin chỉ khi thực sự cần thiết để tư vấn tốt hơn
""" + STYLE_RULES


PRODUCT_SEARCH_TOOLS = [
    {
        "name": "search_products",
        "description": "Tìm kiếm xe theo mô tả tự nhiên. Dùng khi khách cung cấp tiêu chí như 'chở hàng 10 tấn', 'xe chạy đường phố', 'chassis', 'thùng kín'...",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Mô tả nhu cầu của khách hàng để tìm kiếm"
                },
                "limit": {
                    "type": "integer",
                    "description": "Số lượng kết quả tối đa (mặc định 3)",
                    "default": 3
                }
            },
            "required": ["query"]
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
        "name": "search_by_budget",
        "description": "Tìm xe trong ngân sách. Dùng khi khách nêu rõ ngân sách (VNĐ)",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget_vnd": {
                    "type": "number",
                    "description": "Ngân sách của khách (VNĐ)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Số lượng kết quả tối đa (mặc định 3)",
                    "default": 3
                }
            },
            "required": ["budget_vnd"]
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
        "description": "Lấy thông tin chi tiết 1 mẫu xe cụ thể. Dùng khi khách hỏi về mẫu đã được giới thiệu",
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
            products = service.search(
                query=tool_input["query"],
                limit=tool_input.get("limit", 3),
                fallback=True
            )
            return {"products": [_product_to_dict(p) for p in products]}

        elif tool_name == "find_by_model_name":
            products = service.find_model(
                name=tool_input["model_name"],
                limit=tool_input.get("limit", 3)
            )
            if not products:
                return {"products": [], "message": f"Không tìm thấy mẫu '{tool_input['model_name']}'"}
            return {"products": [_product_to_dict(p) for p in products]}

        elif tool_name == "search_by_budget":
            products = service.search_by_budget(
                budget_vnd=tool_input["budget_vnd"],
                limit=tool_input.get("limit", 3)
            )
            if not products:
                return {"products": [], "message": f"Không có xe trong ngân sách {fmt_vnd(tool_input['budget_vnd'])}"}
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

    lines = []
    for key, fact in facts.items():
        value = fact.value if hasattr(fact, 'value') else fact.get('value') if isinstance(fact, dict) else None
        if value is not None:
            lines.append(f"- {key}: {value}")

    return "\n".join(lines) if lines else "Chưa có thông tin về nhu cầu khách."


def sales(state: AgentState) -> dict:
    """Sales agent that uses LLM tool calling to search products."""
    user_message = state.get("task_text") or state.get("user_text", "")
    known_facts_text = _format_known_facts(state)

    context = f"""Tin nhắn của khách: {user_message}

Thông tin đã biết về khách:
{known_facts_text}

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
        reply = f"Xin lỗi, có lỗi khi xử lý: {result['error']}"
    else:
        reply = result.get("final_response", "Xin lỗi, em chưa thể tư vấn được ạ.")

    # Extract products that were found
    products_found = []
    for tool_call in result.get("tool_calls", []):
        tool_result = tool_call.get("result", {})
        if "products" in tool_result:
            for p_dict in tool_result["products"]:
                products_found.append(p_dict)

    update = {
        "trace": step(state, "sales"),
        "answers": [answer(state, "sales", reply)]
    }

    # Track which products were shown
    if products_found:
        selected_entities = list(state.get("selected_entities", []) or [])
        existing_ids = {item.get("entity_id") for item in selected_entities if isinstance(item, dict)}
        for p_dict in products_found:
            if p_dict["id"] not in existing_ids:
                selected_entities.append({
                    "entity_type": "PRODUCT",
                    "entity_id": p_dict["id"],
                    "name": p_dict["name"]
                })
        update["selected_entities"] = selected_entities

        # Track allowed amounts for pricing
        update["allowed_amounts"] = [fmt_vnd(p_dict["list_price_vnd"]) for p_dict in products_found]

    return update

