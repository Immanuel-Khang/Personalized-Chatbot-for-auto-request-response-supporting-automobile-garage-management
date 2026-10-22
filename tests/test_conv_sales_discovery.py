"""Tư vấn mua xe nhiều lượt: hỏi nhu cầu trước khi đưa xe, hiểu câu trả lời ngắn nhờ sales_question,
tra catalog chỉ khi có căn cứ (tên mẫu, ngân sách, xin xem danh mục, hỏi phổ biến).
Test chạy rule-based (LLM_PROVIDER=none); diễn giải LLM được mock qua preprocess.generate_structured."""
from unittest.mock import Mock

from app.agent.nodes.preprocess import _llm_preprocess, merge_sales_slots
from app.contracts.schemas import (CustomerSlots, Intent, IntentAndSlotExtraction, IntentTask, SalesField,
                                   SalesSignals, UseCategory)
from app.db.models import CatalogItem
from app.db.session import SessionLocal
from tests.conversation_helpers import AMOUNT, NOT_UNDERSTOOD, Conversation, questions, unsupported_amounts

BUY = "Tôi muốn mua xe."
USE_QUESTION = ("mục đích", "để làm gì", "sử dụng xe")


def catalog_names() -> list[str]:
    with SessionLocal() as db:
        return [n.lower() for (n,) in db.query(CatalogItem.name)]


def lists_products(reply: str) -> bool:
    lowered = reply.lower()
    return bool(AMOUNT.findall(reply)) or any(n in lowered for n in catalog_names())


def llm(monkeypatch, **kwargs):
    """Mock một kết quả structured output của preprocess."""
    result = IntentAndSlotExtraction(**kwargs)
    mock = Mock(return_value=result)
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", mock)
    return mock


# ---- Hành vi tư vấn ----
def test_broad_purchase_asks_intended_use_without_products(client):
    conv = Conversation(client, "buy")
    result = conv.say(BUY)
    assert "sales" in result["trace"] and not lists_products(result["reply"])
    assert any(k in result["reply"].lower() for k in USE_QUESTION) and questions(result["reply"]) == 1
    assert conv.state["sales_question"]["asked"] == "intended_use"


def test_short_answer_continues_sales_in_same_session(client):
    conv = Conversation(client, "buy-answer")
    conv.say(BUY)
    result = conv.say("Chở hàng.")
    assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert conv.state["slots"]["intended_use"] == "Chở hàng"
    assert not lists_products(result["reply"])  # catalog không có tải trọng → hỏi thêm, không đoán xe phù hợp
    assert "tấn" in result["reply"] or "ngân sách" in result["reply"]


def test_purpose_variants_in_rule_mode(client):
    for i, text in enumerate(["Để phục vụ gia đình.", "Tôi dùng để đi làm hằng ngày.", "Đi lại trong thành phố"]):
        conv = Conversation(client, f"variant{i}")
        conv.say(BUY)
        result = conv.say(text)
        assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
        assert conv.state["slots"].get("intended_use") and not lists_products(result["reply"])
        assert "xe tải" in result["reply"]  # nói rõ phạm vi catalog, không giả là xe con


def test_llm_interprets_unlisted_cargo_phrase(client, monkeypatch):
    llm(monkeypatch, intents=[Intent.SALES],
        tasks=[IntentTask(intent=Intent.SALES, text="Tôi cần xe để vận chuyển vật liệu xây dựng")],
        extracted_slots=CustomerSlots(intended_use="vận chuyển vật liệu xây dựng"),
        sales=SalesSignals(intended_use_stated=True, use_category=UseCategory.CARGO))
    conv = Conversation(client, "llm-cargo")
    result = conv.say("Tôi cần xe để vận chuyển vật liệu xây dựng")
    assert conv.state["slots"]["use_category"] == "CARGO" and not lists_products(result["reply"])
    assert "tấn" in result["reply"]


def test_family_use_is_unsupported_honestly(client, monkeypatch):
    llm(monkeypatch, intents=[Intent.SALES], extracted_slots=CustomerSlots(intended_use="phục vụ gia đình"),
        sales=SalesSignals(intended_use_stated=True, use_category=UseCategory.PASSENGER))
    result = Conversation(client, "family").say("Tôi cần xe phục vụ gia đình")
    assert "chưa có xe chở người" in result["reply"] and not lists_products(result["reply"])


def test_explicit_browse_is_stable_and_scoped(client):
    first = Conversation(client, "browse1").say("Cho tôi xem danh sách xe tải.")
    second = Conversation(client, "browse2").say("Tôi muốn xem tất cả các mẫu xe bên mình")
    assert "sales" in first["trace"] and "55 mẫu xe tải" in first["reply"]
    assert AMOUNT.findall(first["reply"]) == AMOUNT.findall(second["reply"]) != []
    assert unsupported_amounts(first["reply"]) == []


def test_browse_offer_accepted_with_yes(client):
    conv = Conversation(client, "vios")
    no_match = conv.say("Tôi muốn mua xe Toyota Vios")
    assert "chưa có mẫu" in no_match["reply"] and AMOUNT.findall(no_match["reply"]) == []  # không thay xe khác
    assert conv.state["sales_question"]["asked"] == "browse_offer"
    listed = conv.say("Có")
    assert "sales" in listed["trace"] and "mẫu xe tải" in listed["reply"]


def test_specific_model_lookup(client):
    result = Conversation(client, "fuso").say("Tôi muốn tìm xe FUSO Canter FI170")
    assert "fuso canter fi170" in result["reply"].lower() and unsupported_amounts(result["reply"]) == []


def test_budget_and_popular_not_blocked(client):
    budget = Conversation(client, "budget").say("Ngân sách của tôi là 500 triệu")
    assert "sales" in budget["trace"] and AMOUNT.findall(budget["reply"])
    assert "ngân sách" in budget["reply"]
    popular = Conversation(client, "popular").say("Bên mình có mẫu xe nào phổ biến?")
    assert AMOUNT.findall(popular["reply"]) and "chưa có số liệu bán chạy" in popular["reply"]


def test_failed_search_returns_nothing(client):
    from app.services import get_services
    product = get_services().product
    assert product.search("Tôi muốn mua xe", fallback=False) == []
    assert product.find_model("Toyota Vios") == []
    assert len(product.search("Tôi muốn mua xe")) == 3  # caller cũ (fallback mặc định) không đổi


# ---- Trạng thái và ngữ cảnh ----
def test_requirements_persist_and_correct(client, monkeypatch):
    conv = Conversation(client, "persist")
    conv.say(BUY)
    conv.say("Chở hàng")
    conv.say("Ngân sách 700 triệu")
    slots = conv.state["slots"]
    assert slots["intended_use"] == "Chở hàng" and slots["budget_vnd"] == 700_000_000
    llm(monkeypatch, intents=[Intent.SALES], extracted_slots=CustomerSlots(intended_use="phục vụ gia đình"),
        sales=SalesSignals(intended_use_stated=True, use_category=UseCategory.PASSENGER))
    conv.say("Thực ra tôi cần xe phục vụ gia đình")
    slots = conv.state["slots"]
    assert slots["intended_use"] == "phục vụ gia đình" and slots["use_category"] == "PASSENGER"
    assert slots["budget_vnd"] == 700_000_000  # trường không nhắc tới không bị xóa


def test_sessions_do_not_share_sales_state(client):
    a, b = Conversation(client, "iso-a"), Conversation(client, "iso-b")
    a.say(BUY)
    a.say("Chở hàng")
    assert b.state.get("sales_question") is None
    result = b.say("Chở hàng")
    assert "sales" not in result["trace"] and not (b.state.get("slots") or {}).get("intended_use")


def test_pending_question_does_not_hijack_other_turns(client):
    for i, text in enumerate(["Xin chào", "Thôi", "Phanh kêu là tại sao?", "Cảm ơn"]):
        conv = Conversation(client, f"hijack{i}")
        conv.say(BUY)
        result = conv.say(text)
        assert "sales" not in result["trace"] and not conv.state["slots"].get("intended_use")
        assert conv.state.get("sales_question") is None  # câu hỏi chờ hết hạn sau một lượt


def test_unsure_is_not_a_comprehension_failure(client):
    conv = Conversation(client, "unsure")
    conv.say(BUY)
    result = conv.say("Tôi chưa biết")
    assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert not conv.state["slots"].get("intended_use")


def test_merge_policy():
    old = {"intended_use": "chở hàng", "use_category": "CARGO", "budget_vnd": 7e8, "customer_name": "A"}
    # suy đoán (intended_use_stated=False, không trả lời câu hỏi mục đích) không ghi đè
    merged, _ = merge_sales_slots(old, dict(old), {"intended_use": "gia đình"}, SalesSignals(), None)
    assert merged["intended_use"] == "chở hàng"
    # bỏ yêu cầu rõ ràng
    merged, _ = merge_sales_slots(old, dict(old), {}, SalesSignals(cleared_fields=[SalesField.BUDGET]), None)
    assert "budget_vnd" not in merged and merged["intended_use"] == "chở hàng"
    # mục đích mới không có nhóm → nhóm cũ bị bỏ, không giữ CARGO sai
    merged, _ = merge_sales_slots(old, dict(old), {"intended_use": "đi làm"}, SalesSignals(intended_use_stated=True), None)
    assert merged["intended_use"] == "đi làm" and "use_category" not in merged
    # 'trả lời câu hỏi chờ' khi không có câu hỏi chờ → bị bỏ
    _, signals = merge_sales_slots({}, {}, {}, SalesSignals(answers_pending_question=True), None)
    assert not signals["answered"]
    # giá trị sai kiểu/âm không vào state
    merged, _ = merge_sales_slots({}, {}, {"budget_vnd": -5, "payload_tons": 500}, SalesSignals(), None)
    assert merged == {}


# ---- LLM và fallback ----
def test_llm_receives_pending_question_and_known_requirements(monkeypatch):
    mock = llm(monkeypatch, intents=[Intent.OTHER], extracted_slots=CustomerSlots(intended_use="chở hàng"),
               sales=SalesSignals(answers_pending_question=True))
    state = {"user_text": "Chở hàng", "slots": {"budget_vnd": 7e8},
             "sales_question": {"asked": "intended_use", "question": "Anh/chị dùng xe để làm gì?"}}
    output = _llm_preprocess(state)
    message = mock.call_args.args[1]
    assert "Anh/chị dùng xe để làm gì?" in message and message.rstrip().endswith("Chở hàng")
    assert output["intents"] == ["SALES"] and output["tasks"] == [{"intent": "SALES", "text": "Chở hàng"}]
    assert output["slots"]["intended_use"] == "chở hàng" and output["sales_signals"]["source"] == "llm"


def test_llm_inferred_use_without_question_is_dropped(monkeypatch):
    llm(monkeypatch, intents=[Intent.SALES], extracted_slots=CustomerSlots(intended_use="chở hàng"),
        sales=SalesSignals(use_category=UseCategory.CARGO))
    output = _llm_preprocess({"user_text": BUY, "slots": {}})
    assert "intended_use" not in output["slots"] and "use_category" not in output["slots"]


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=None))
    state = {"user_text": "Chở hàng", "slots": {}, "sales_question": {"asked": "intended_use", "question": "?"}}
    output = _llm_preprocess(state)
    assert output["sales_signals"]["source"] == "rules" and output["slots"]["intended_use"] == "Chở hàng"
    assert "use_category" not in output["slots"]  # fallback không suy ra nhóm mục đích


# ---- Hồi quy với các luồng khác ----
def test_sales_plus_test_drive_asks_only_booking_question(client):
    conv = Conversation(client, "buy-drive")
    result = conv.say("Tôi muốn mua xe và đặt lịch lái thử")
    assert {"sales", "appointment"} <= set(result["trace"]) and questions(result["reply"]) == 1
    assert conv.state.get("sales_question") is None and conv.state["active_flow"] == "appointment"


def test_sales_question_keeps_booking_slots_of_active_flow(client):
    conv = Conversation(client, "flow-keep")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai")
    conv.say(BUY)
    assert conv.state["slots"].get("service_type") == "MAINTENANCE"
