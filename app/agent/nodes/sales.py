"""Read-only vehicle sales specialist backed by canonical conversation state."""
from enum import Enum
import uuid

from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.nodes.product_reference import resolve_product
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import (
    InteractionKind,
    InteractionStatus,
    KnownFact,
    PendingInteraction,
    Product,
)
from app.services import get_services
from app.utils import fmt_vnd


SUGGESTION_KEYWORDS = ("phổ biến", "bán chạy", "gợi ý", "nên mua", "nào tốt", "xe gì", "dòng xe nào", "loại xe nào")
PURCHASE_WORDS = ("mua xe", "tìm xe", "mẫu xe", "dòng xe", "chiếc xe")
WRITERS = ("contract", "appointment")
BROWSE_LIMIT = 10
KIND_LABEL = {"TRUCK": "xe tải", "CAR": "xe con"}


SALES_SYSTEM_PROMPT = """\
Bạn là trợ lý tư vấn xe của xưởng Foton chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Khi nêu giá, PHẢI dùng đúng số liệu được cung cấp, KHÔNG ĐƯỢC tự bịa giá.
3. Kèm ngày hiệu lực giá (price_as_of) khi báo giá.
4. KHÔNG hứa giảm giá, quà tặng hay cam kết nào.
5. Chỉ trả lời phần câu hỏi được giao; KHÔNG tự hỏi ngày giờ hay số điện thoại đặt lịch.
6. Chỉ dùng thông tin khách hàng được cung cấp trong phần dữ liệu đã biết.
7. KHÔNG khẳng định xe phù hợp nhu cầu khi dữ liệu xe không có thông số đó.
""" + STYLE_RULES


class SalesMode(str, Enum):
    MODEL_LOOKUP = "MODEL_LOOKUP"
    NO_MATCH = "NO_MATCH"
    BUDGET_SEARCH = "BUDGET_SEARCH"
    CATALOG_BROWSE = "CATALOG_BROWSE"
    POPULAR_PRODUCTS = "POPULAR_PRODUCTS"
    UNSUPPORTED_USE = "UNSUPPORTED_USE"
    NEEDS_DETAILS = "NEEDS_DETAILS"
    UNCERTAIN = "UNCERTAIN"
    DISCOVERY = "DISCOVERY"
    PRODUCT_DETAIL = "PRODUCT_DETAIL"
    CLARIFY_PRODUCT = "CLARIFY_PRODUCT"


def _fact_value(facts: dict, key: str):
    fact = facts.get(key)
    if isinstance(fact, KnownFact):
        return fact.value
    return fact.get("value") if isinstance(fact, dict) else None


def _facts_as_values(state: AgentState) -> dict:
    return {key: _fact_value(state.get("known_facts", {}) or {}, key)
            for key in (state.get("known_facts", {}) or {})}


def _pending(state: AgentState) -> list[PendingInteraction]:
    result = []
    for item in state.get("pending_interactions", []) or []:
        pending = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if pending.status == InteractionStatus.OPEN:
            result.append(pending)
    return result


def _replace_sales_pending(state: AgentState, interaction: PendingInteraction | None) -> list[PendingInteraction]:
    result = []
    for item in _pending(state):
        if item.metadata.get("owner") == "sales":
            result.append(item.model_copy(update={"status": InteractionStatus.EXPIRED}))
        else:
            result.append(item)
    if interaction:
        result.append(interaction)
    return result


def _selected_products(state: AgentState) -> list[Product]:
    service = get_services().product
    products = []
    for item in state.get("selected_entities", []) or []:
        if not isinstance(item, dict) or item.get("entity_type") != "PRODUCT":
            continue
        product_id = item.get("entity_id")
        if isinstance(product_id, int) and (product := service.get_by_id(product_id)):
            products.append(product)
    return products


def _with_products(state: AgentState, products: list[Product]) -> dict:
    selected = list(state.get("selected_entities", []) or [])
    existing = {item.get("entity_id") for item in selected if isinstance(item, dict)}
    selected.extend({"entity_type": "PRODUCT", "entity_id": product.id, "name": product.name}
                    for product in products if product.id not in existing)
    return {"selected_entities": selected}


def _scope() -> str:
    summary = get_services().product.vehicle_summary()
    return ", ".join(f"{count} mẫu {KIND_LABEL.get(kind, kind.lower())}"
                      for kind, count in summary.items()) or "chưa có mẫu xe"


def _only_trucks() -> bool:
    return set(get_services().product.vehicle_summary()) == {"TRUCK"}


def _use_text(facts: dict) -> str:
    return str(_fact_value(facts, "intended_use") or "").strip().rstrip(".!?").lower()


def _history_assistant_messages(state: AgentState) -> list[str]:
    return [str(item.get("content", "")) for item in state.get("history", []) or []
            if isinstance(item, dict) and item.get("role") == "ai"]


def sales_mode(state: AgentState) -> tuple[SalesMode, list[Product]]:
    """Choose a catalog operation without inferring facts or selecting unseen products."""
    facts = state.get("known_facts", {}) or {}
    interpretation = state.get("conversation_interpretation")
    question = state.get("task_text") or state.get("user_text", "")
    text = question.casefold()
    service = get_services().product

    if interpretation and interpretation.product_reference_ambiguous:
        return SalesMode.CLARIFY_PRODUCT, []
    if interpretation and interpretation.referenced_product_name:
        shown = _selected_products(state)
        product, ambiguous = resolve_product(
            question, _history_assistant_messages(state), shown,
            interpretation.referenced_product_name,
        )
        return ((SalesMode.PRODUCT_DETAIL, [product]) if product and not ambiguous
                else (SalesMode.CLARIFY_PRODUCT, []))
    if interpretation and interpretation.catalog_request:
        return SalesMode.CATALOG_BROWSE, service.list_vehicles(limit=BROWSE_LIMIT)

    car_model = _fact_value(facts, "car_model")
    if car_model:
        products = service.find_model(str(car_model), limit=3)
        return (SalesMode.MODEL_LOOKUP, products) if products else (SalesMode.NO_MATCH, [])
    budget = _fact_value(facts, "budget_vnd")
    if budget is not None:
        return SalesMode.BUDGET_SEARCH, service.search_by_budget(float(budget), limit=3)
    if any(keyword in text for keyword in SUGGESTION_KEYWORDS):
        return SalesMode.POPULAR_PRODUCTS, service.get_popular(limit=3)

    intended_use = _fact_value(facts, "intended_use")
    if intended_use:
        if _fact_value(facts, "use_category") == "PASSENGER":
            return SalesMode.UNSUPPORTED_USE, []
        return SalesMode.NEEDS_DETAILS, []
    if interpretation and interpretation.uncertain:
        return SalesMode.UNCERTAIN, []
    return SalesMode.DISCOVERY, []


def _discovery_reply(mode: SalesMode, facts: dict) -> tuple[str, str, str | None]:
    use = _use_text(facts)
    if mode == SalesMode.DISCOVERY:
        return ("Dạ, em có thể hỗ trợ anh/chị chọn xe phù hợp ạ.",
                "Anh/chị dự định sử dụng xe chủ yếu để làm gì, ví dụ chở hàng, phục vụ gia đình hay đi lại hằng ngày?",
                "intended_use")
    if mode == SalesMode.UNCERTAIN:
        return (f"Dạ không sao ạ. Hiện catalog bên em có {_scope()}.",
                "Anh/chị cho em biết loại hàng hoặc khối lượng thường chở, ngân sách dự kiến, hoặc em gửi danh sách các mẫu để anh/chị tham khảo nhé?",
                "intended_use")
    if mode == SalesMode.UNSUPPORTED_USE:
        return (f"Dạ, em ghi nhận anh/chị cần xe để {use} ạ. Hiện catalog bên em chỉ có {_scope()}, chưa có xe chở người/xe gia đình nên em chưa thể gợi ý mẫu phù hợp.",
                f"Anh/chị có muốn xem danh sách các mẫu xe hiện có, hoặc gọi hotline {settings.hotline} để nhân viên tư vấn thêm không?",
                "browse_offer")
    if mode == SalesMode.NO_MATCH:
        model = _fact_value(facts, "car_model") or "anh/chị nêu"
        return (f"Dạ, hiện catalog bên em chưa có mẫu {model} ạ.",
                "Anh/chị có muốn xem danh sách các mẫu xe đang có không?", "browse_offer")
    known = f"Dạ, em ghi nhận nhu cầu {use} ạ. Hiện catalog bên em có {_scope()}"
    if _fact_value(facts, "use_category") != "CARGO" and _only_trucks():
        return (known + ", chưa có xe chở người/xe gia đình.",
                "Nếu anh/chị cần xe tải chở hàng, cho em biết tải trọng và ngân sách dự kiến; hoặc em gửi danh sách các mẫu xe hiện có để anh/chị tham khảo nhé?",
                "cargo_details")
    payload = _fact_value(facts, "payload_tons")
    if payload is not None:
        return (known + f". Dữ liệu xe chưa có thông số tải trọng chi tiết để lọc theo {payload:g} tấn.",
                "Anh/chị cho em biết ngân sách dự kiến, hoặc em gửi danh sách các mẫu để anh/chị tham khảo nhé?",
                "cargo_details")
    return (known + ".", "Anh/chị thường chở khoảng bao nhiêu tấn hàng và ngân sách dự kiến khoảng bao nhiêu ạ?", "cargo_details")


def _product_line(product: Product, budget: float | None = None) -> str:
    specs = ", ".join(f"{key}: {value}" for key, value in product.specs.items()) if product.specs else ""
    label = " [trong ngân sách]" if budget and product.list_price_vnd <= budget else " [vượt ngân sách]" if budget else ""
    return f"- {product.name}: giá niêm yết {fmt_vnd(product.list_price_vnd)} (cập nhật {product.price_as_of}){label}" + (f" | {specs}" if specs else "")


MODE_GUIDE = {
    SalesMode.MODEL_LOOKUP: "Đây là các mẫu có tên khớp với từ khách nêu. Không giới thiệu mẫu thay thế như phù hợp.",
    SalesMode.BUDGET_SEARCH: "Chỉ nói xe nằm trong ngân sách khi được đánh dấu [trong ngân sách]; xe [vượt ngân sách] phải nói rõ.",
    SalesMode.POPULAR_PRODUCTS: "Chưa có số liệu bán chạy; giới thiệu đây là một số mẫu trong catalog, không nói là phổ biến nhất.",
    SalesMode.PRODUCT_DETAIL: "Chỉ mô tả đúng mẫu xe đã xác định bằng dữ liệu được cung cấp; không bịa thông số.",
}
FALLBACK_INTRO = {
    SalesMode.MODEL_LOOKUP: "Mình tìm thấy các mẫu có tên khớp với yêu cầu:",
    SalesMode.BUDGET_SEARCH: "Các mẫu xe theo ngân sách bạn nêu:",
    SalesMode.POPULAR_PRODUCTS: "Hệ thống chưa có số liệu bán chạy; đây là một số mẫu trong catalog:",
    SalesMode.PRODUCT_DETAIL: "Dạ, thông tin mẫu xe anh/chị hỏi theo catalog:",
}
CLARIFY_PRODUCT_REPLY = "Dạ, anh/chị đang hỏi mẫu xe nào ạ? Anh/chị cho em xin tên mẫu hoặc số thứ tự trong danh sách em vừa gửi nhé."


def sales(state: AgentState) -> dict:
    facts = state.get("known_facts", {}) or {}
    question = state.get("task_text") or state.get("user_text", "")
    mode, products = sales_mode(state)
    active_workflow = state.get("active_workflow")
    writer_owns_turn = bool((active_workflow is not None and active_workflow.workflow_type in WRITERS)
                            or any(agent in WRITERS for agent in state.get("planned_agents", [])))
    update: dict = {"trace": step(state, "sales")}

    if mode == SalesMode.CATALOG_BROWSE:
        if not products:
            return {**update, "answers": [answer(state, "sales", "Hiện catalog chưa có mẫu xe nào để giới thiệu ạ.")]}
        reply = (f"Dạ, hiện catalog bên em có {_scope()}. Dưới đây là {len(products)} mẫu đầu theo danh mục (chưa phải gợi ý theo nhu cầu):\n"
                 + "\n".join(_product_line(product) for product in products))
        if not writer_owns_turn:
            reply += "\nAnh/chị cho em biết mục đích sử dụng hoặc ngân sách để em lọc giúp nhé."
        return {**update, **_with_products(state, products), "answers": [answer(state, "sales", reply)],
                "allowed_amounts": [fmt_vnd(product.list_price_vnd) for product in products]}

    if mode == SalesMode.CLARIFY_PRODUCT:
        return {**update, "answers": [answer(state, "sales", CLARIFY_PRODUCT_REPLY)]}

    if not products:
        statement, ask, asked = _discovery_reply(mode, facts)
        if writer_owns_turn:
            # Writer owns turn: provide informative statement without creating pending question
            combined = f"{statement} {ask}" if statement else ask
            return {**update, "answers": [answer(state, "sales", combined)]}
        pending = PendingInteraction(
            interaction_id=f"sales_{uuid.uuid4().hex[:8]}",
            kind=InteractionKind.QUESTION if asked != "browse_offer" else InteractionKind.OFFER,
            text=ask, status=InteractionStatus.OPEN, related_task_id=None,
            created_at_message_id=None, metadata={"owner": "sales", "asked": asked},
        )
        return {**update, "answers": [answer(state, "sales", f"{statement} {ask}")],
                "pending_interactions": _replace_sales_pending(state, pending)}

    budget_value = _fact_value(facts, "budget_vnd") if mode == SalesMode.BUDGET_SEARCH else None
    budget = float(budget_value) if budget_value is not None else None
    product_info = "\n".join(_product_line(product, budget) for product in products)
    llm_reply = generate(
        SALES_SYSTEM_PROMPT,
        f"Câu hỏi của khách: {question}\n\nDữ liệu xe từ hệ thống:\n{product_info}\n\n"
        f"Lưu ý: {MODE_GUIDE[mode]}\nPhạm vi catalog: {_scope()}\n"
        f"Thông tin đã biết về khách: {_facts_as_values(state)}\nHãy tư vấn dựa trên dữ liệu trên.",
        history=state.get("history"),
    )
    reply = llm_reply or (f"{FALLBACK_INTRO[mode]}\n{product_info}\nBạn muốn mình tư vấn kỹ hơn hay đặt lịch lái thử không?")
    return {**update, **_with_products(state, products), "answers": [answer(state, "sales", reply)],
            "allowed_amounts": [fmt_vnd(product.list_price_vnd) for product in products]}
