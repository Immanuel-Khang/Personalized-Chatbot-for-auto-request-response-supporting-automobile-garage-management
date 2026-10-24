"""[TRACK B] Sales Agent: tra cứu giá từ DB, dùng LLM sinh câu trả lời tự nhiên.
Chỉ đọc, không có quyền về giá: chỉ báo giá niêm yết kèm ngày hiệu lực.
Mặc cả do Policy Guard xử lý; chỉ đọc dữ liệu, chạy song song với các agent khác.

Quyết định bằng code (sales_mode) TRƯỚC khi tra catalog: chỉ lấy xe khi có căn cứ (tên mẫu, ngân sách,
khách xin xem danh mục, hỏi mẫu phổ biến). Chưa rõ nhu cầu thì hỏi mục đích sử dụng, không đưa xe đầu catalog.
Câu hỏi tư vấn vừa hỏi được lưu ở sales_question để lượt sau hiểu câu trả lời ngắn ('Chở hàng')."""
from enum import Enum

from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import Product, UseCategory
from app.services import get_services
from app.utils import fmt_vnd

SUGGESTION_KEYWORDS = ["phổ biến", "bán chạy", "gợi ý", "nên mua", "nào tốt", "xe gì", "dòng xe nào", "loại xe nào"]
PURCHASE_WORDS = ("mua xe", "tìm xe", "mẫu xe", "dòng xe", "chiếc xe")
WRITERS = ("contract", "appointment")  # cùng tên với router.WRITERS (import router ở đây sẽ thành vòng)
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
6. Sử dụng thông tin về khách hàng (slots) để tư vấn cá nhân hóa, KHÔNG hỏi lại thông tin đã có.
7. KHÔNG khẳng định xe phù hợp nhu cầu (tải trọng, chở người...) khi dữ liệu xe không có thông số đó.
""" + STYLE_RULES


class SalesMode(str, Enum):
    MODEL_LOOKUP = "MODEL_LOOKUP"  # khách nêu tên mẫu / tên khớp catalog
    NO_MATCH = "NO_MATCH"  # khách nêu tên mẫu nhưng catalog không có
    BUDGET_SEARCH = "BUDGET_SEARCH"
    CATALOG_BROWSE = "CATALOG_BROWSE"  # khách chủ động xin xem danh mục
    POPULAR_PRODUCTS = "POPULAR_PRODUCTS"
    UNSUPPORTED_USE = "UNSUPPORTED_USE"  # nhu cầu chở người/gia đình, catalog không có xe phù hợp
    NEEDS_DETAILS = "NEEDS_DETAILS"  # đã biết mục đích, thiếu tải trọng/ngân sách để lọc
    UNCERTAIN = "UNCERTAIN"  # khách nói chưa biết nhu cầu
    DISCOVERY = "DISCOVERY"  # mới chỉ muốn mua xe: hỏi mục đích sử dụng
    PRODUCT_DETAIL = "PRODUCT_DETAIL"  # khách nhắc mẫu bot đã hiển thị ('xe thứ 3', 'nó'): tra đúng id
    CLARIFY_PRODUCT = "CLARIFY_PRODUCT"  # tham chiếu mơ hồ / không có danh sách → hỏi lại, không đoán xe


def sales_mode(state: AgentState) -> tuple[SalesMode, list[Product]]:
    """Chính sách mua xe bằng code: chọn MỘT cách xử lý và chỉ tra catalog khi cách đó cho phép."""
    slots = state.get("slots", {})
    signals = state.get("sales_signals") or {}
    question = state.get("task_text") or state.get("user_text", "")
    text = question.lower()
    svc = get_services().product
    followup = state.get("followup") or {}

    if product_id := followup.get("product_id"):  # followup.py đã đối chiếu với lịch sử: tra catalog theo id
        product = svc.get_by_id(product_id)
        return (SalesMode.PRODUCT_DETAIL, [product]) if product else (SalesMode.CLARIFY_PRODUCT, [])
    if followup.get("clarify_product"):
        return SalesMode.CLARIFY_PRODUCT, []
    if signals.get("catalog_request"):
        return SalesMode.CATALOG_BROWSE, svc.list_vehicles(limit=BROWSE_LIMIT)
    if car_model := slots.get("car_model"):
        products = svc.find_model(car_model, limit=3)
        if signals.get("model_mentioned"):  # khách vừa nêu tên mẫu: không có thì nói thật, không thay xe khác
            return (SalesMode.MODEL_LOOKUP, products) if products else (SalesMode.NO_MATCH, [])
        if products and not signals.get("answered"):
            return SalesMode.MODEL_LOOKUP, products
    if matched := svc.search(question, limit=3, fallback=False):  # tên mẫu trong câu ('Giá xe Foton Aumark')
        if any(k in text for k in PURCHASE_WORDS):  # khách tìm XE: phụ tùng trùng hãng ('Toyota') không phải mẫu xe
            vehicles = [p for p in matched if p.kind != "PART"]
            if not vehicles:
                return SalesMode.NO_MATCH, []
            matched = vehicles
        return SalesMode.MODEL_LOOKUP, matched
    if budget := slots.get("budget_vnd"):
        return SalesMode.BUDGET_SEARCH, svc.search_by_budget(budget, limit=3)
    if any(k in text for k in SUGGESTION_KEYWORDS):
        return SalesMode.POPULAR_PRODUCTS, svc.get_popular(limit=3)
    if slots.get("intended_use"):
        if slots.get("use_category") == UseCategory.PASSENGER.value:
            return SalesMode.UNSUPPORTED_USE, []
        return SalesMode.NEEDS_DETAILS, []
    if signals.get("uncertain"):
        return SalesMode.UNCERTAIN, []
    return SalesMode.DISCOVERY, []


def _scope() -> str:
    """'55 mẫu xe tải' theo dữ liệu thật của catalog."""
    summary = get_services().product.vehicle_summary()
    return ", ".join(f"{n} mẫu {KIND_LABEL.get(kind, kind.lower())}" for kind, n in summary.items()) or "chưa có mẫu xe"


def _only_trucks() -> bool:
    return set(get_services().product.vehicle_summary()) == {"TRUCK"}


def _use_text(slots: dict) -> str:
    return str(slots.get("intended_use", "")).strip().rstrip(".!?").lower()


def _discovery_reply(mode: SalesMode, state: AgentState) -> tuple[str, str, str | None]:
    """(câu nói, câu hỏi, loại câu hỏi chờ trả lời). Câu hỏi bị bỏ khi agent ghi dữ liệu đang hỏi slot cùng lượt."""
    slots = state.get("slots", {})
    use = _use_text(slots)
    if mode == SalesMode.DISCOVERY:
        return ("Dạ, em có thể hỗ trợ anh/chị chọn xe phù hợp ạ.",
                "Anh/chị dự định sử dụng xe chủ yếu để làm gì, ví dụ chở hàng, phục vụ gia đình hay đi lại hằng ngày?",
                "intended_use")
    if mode == SalesMode.UNCERTAIN:
        return (f"Dạ không sao ạ. Hiện catalog bên em có {_scope()}.",
                "Anh/chị cho em biết loại hàng hoặc khối lượng thường chở, ngân sách dự kiến, "
                "hoặc em gửi danh sách các mẫu để anh/chị tham khảo nhé?", "intended_use")
    if mode == SalesMode.UNSUPPORTED_USE:
        return (f"Dạ, em ghi nhận anh/chị cần xe để {use} ạ. Hiện catalog bên em chỉ có {_scope()}, "
                "chưa có xe chở người/xe gia đình nên em chưa thể gợi ý mẫu phù hợp.",
                f"Anh/chị có muốn xem danh sách các mẫu xe hiện có, hoặc gọi hotline {settings.hotline} "
                "để nhân viên tư vấn thêm không?", "browse_offer")
    if mode == SalesMode.NO_MATCH:
        model = slots.get("car_model") or "anh/chị nêu"
        return (f"Dạ, hiện catalog bên em chưa có mẫu {model} ạ.",
                "Anh/chị có muốn xem danh sách các mẫu xe đang có không?", "browse_offer")
    # NEEDS_DETAILS: đã biết mục đích nhưng catalog không có tiêu chí để xếp hạng → hỏi đúng thông tin còn thiếu
    known = f"Dạ, em ghi nhận nhu cầu {use} ạ. Hiện catalog bên em có {_scope()}"
    if slots.get("use_category") != UseCategory.CARGO.value and _only_trucks():
        # chưa rõ nhóm nhu cầu (fallback không có LLM): nói rõ phạm vi, không đoán xe tải là phù hợp
        return (known + ", chưa có xe chở người/xe gia đình.",
                "Nếu anh/chị cần xe tải chở hàng, cho em biết tải trọng và ngân sách dự kiến; "
                "hoặc em gửi danh sách các mẫu xe hiện có để anh/chị tham khảo nhé?", "cargo_details")
    if payload := slots.get("payload_tons"):
        return (known + f". Dữ liệu xe chưa có thông số tải trọng chi tiết để lọc theo {payload:g} tấn.",
                "Anh/chị cho em biết ngân sách dự kiến, hoặc em gửi danh sách các mẫu để anh/chị tham khảo nhé?",
                "cargo_details")
    return (known + ".", "Anh/chị thường chở khoảng bao nhiêu tấn hàng và ngân sách dự kiến khoảng bao nhiêu ạ?",
            "cargo_details")


def _product_line(p: Product, budget: float | None = None) -> str:
    specs = ", ".join(f"{k}: {v}" for k, v in p.specs.items()) if p.specs else ""
    label = ""
    if budget:
        label = " [trong ngân sách]" if p.list_price_vnd <= budget else " [vượt ngân sách]"
    return (f"- {p.name}: giá niêm yết {fmt_vnd(p.list_price_vnd)} (cập nhật {p.price_as_of}){label}"
            + (f" | {specs}" if specs else ""))


MODE_GUIDE = {
    SalesMode.MODEL_LOOKUP: ("Đây là các mẫu có tên khớp với từ khách nêu. Nếu không có đúng mẫu khách hỏi, "
                             "nói rõ catalog chưa có mẫu đó, KHÔNG giới thiệu như mẫu thay thế phù hợp."),
    SalesMode.BUDGET_SEARCH: ("Xe được tìm theo ngân sách. Chỉ nói xe nằm trong ngân sách khi được đánh dấu "
                              "[trong ngân sách]; xe [vượt ngân sách] phải nói rõ là vượt."),
    SalesMode.POPULAR_PRODUCTS: ("Hệ thống CHƯA có số liệu bán chạy: giới thiệu đây là một số mẫu trong catalog, "
                                 "KHÔNG nói là bán chạy hay phổ biến nhất."),
    SalesMode.PRODUCT_DETAIL: ("Khách hỏi về ĐÚNG mẫu xe này (đã xác định từ danh sách trước). Chỉ mô tả mẫu này bằng "
                               "dữ liệu được cung cấp; thông số không có thì nói chưa có, KHÔNG bịa."),
}
FALLBACK_INTRO = {
    SalesMode.MODEL_LOOKUP: "Mình tìm thấy các mẫu có tên khớp với yêu cầu:",
    SalesMode.BUDGET_SEARCH: "Các mẫu xe theo ngân sách bạn nêu:",
    SalesMode.POPULAR_PRODUCTS: "Hệ thống chưa có số liệu bán chạy; đây là một số mẫu trong catalog:",
    SalesMode.PRODUCT_DETAIL: "Dạ, thông tin mẫu xe anh/chị hỏi theo catalog:",
}
CLARIFY_PRODUCT_REPLY = "Dạ, anh/chị đang hỏi mẫu xe nào ạ? Anh/chị cho em xin tên mẫu hoặc số thứ tự trong danh sách em vừa gửi nhé."


def sales(state: AgentState) -> dict:
    slots = state.get("slots", {})
    question = state.get("task_text") or state.get("user_text", "")
    mode, products = sales_mode(state)
    # Agent ghi dữ liệu (đặt lịch/hợp đồng) đang hỏi slot cùng lượt → sales không hỏi thêm, không mở câu hỏi chờ
    writer_owns_turn = bool(state.get("active_flow")) or any(a in WRITERS for a in state.get("planned_agents", []))
    update: dict = {"trace": step(state, "sales")}

    if mode == SalesMode.CATALOG_BROWSE:
        if not products:
            return {**update, "answers": [answer(state, "sales", "Hiện catalog chưa có mẫu xe nào để giới thiệu ạ.")]}
        reply = (f"Dạ, hiện catalog bên em có {_scope()}. Dưới đây là {len(products)} mẫu đầu theo danh mục "
                 "(chưa phải gợi ý theo nhu cầu):\n" + "\n".join(_product_line(p) for p in products))
        if not writer_owns_turn:
            reply += "\nAnh/chị cho em biết mục đích sử dụng hoặc ngân sách để em lọc giúp nhé."
        return {**update, "answers": [answer(state, "sales", reply)],
                "allowed_amounts": [fmt_vnd(p.list_price_vnd) for p in products]}

    if mode == SalesMode.CLARIFY_PRODUCT:  # không đoán xe: không đưa số liệu, hỏi đúng một câu
        return {**update, "answers": [answer(state, "sales", CLARIFY_PRODUCT_REPLY)]}

    if not products:  # các chế độ không tra catalog: hỏi đúng một câu, không đưa xe
        statement, ask, asked = _discovery_reply(mode, state)
        if writer_owns_turn:
            return {**update, "answers": [answer(state, "sales", statement)]}
        return {**update, "answers": [answer(state, "sales", f"{statement} {ask}")],
                "sales_question": {"asked": asked, "question": ask}}

    budget = slots.get("budget_vnd") if mode == SalesMode.BUDGET_SEARCH else None
    product_info = "\n".join(_product_line(p, budget) for p in products)
    llm_reply = generate(
        SALES_SYSTEM_PROMPT,
        f"Câu hỏi của khách: {question}\n\n"
        f"Dữ liệu xe từ hệ thống:\n{product_info}\n\n"
        f"Lưu ý: {MODE_GUIDE[mode]}\n"
        f"Phạm vi catalog: {_scope()}\n"
        f"Thông tin đã biết về khách: {slots}\n"
        f"Hãy tư vấn dựa trên dữ liệu trên.",
        history=state.get("history"),
    )
    reply = llm_reply or (f"{FALLBACK_INTRO[mode]}\n" + product_info +
                          "\nBạn muốn mình tư vấn kỹ hơn hay đặt lịch lái thử không?")

    # Không tự gợi ý đặt lịch: nếu khách muốn lái thử, appointment chạy cùng lượt và hỏi slot ở cuối
    return {**update, "answers": [answer(state, "sales", reply)],
            "allowed_amounts": [fmt_vnd(p.list_price_vnd) for p in products]}
