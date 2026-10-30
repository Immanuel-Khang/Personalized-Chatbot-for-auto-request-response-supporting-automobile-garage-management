"""Sales discovery regressions against canonical conversation interpretation."""
from app.db.models import CatalogItem
from app.db.session import SessionLocal
from tests.conversation_helpers import AMOUNT, NOT_UNDERSTOOD, Conversation, questions, unsupported_amounts

BUY = "Tôi muốn mua xe."
USE_QUESTION = ("mục đích", "để làm gì", "sử dụng xe")


def catalog_names() -> list[str]:
    with SessionLocal() as db:
        return [name.lower() for (name,) in db.query(CatalogItem.name)]


def lists_products(reply: str) -> bool:
    lowered = reply.lower()
    return bool(AMOUNT.findall(reply)) or any(name in lowered for name in catalog_names())


def _fact(state, key):
    fact = state.get("known_facts", {}).get(key)
    return fact.value if fact else None


def test_broad_purchase_asks_intended_use_without_products(client):
    conv = Conversation(client, "buy")
    result = conv.say(BUY)
    assert "sales" in result["trace"] and not lists_products(result["reply"])
    assert any(key in result["reply"].lower() for key in USE_QUESTION) and questions(result["reply"]) == 1
    assert any(item.kind.value == "QUESTION" and item.metadata.get("field") == "intended_use"
               for item in conv.state.get("pending_interactions", []))


def test_short_answer_continues_sales_in_same_session(client):
    conv = Conversation(client, "buy-answer")
    conv.say(BUY)
    result = conv.say("Chở hàng.")
    assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert _fact(conv.state, "intended_use") == "Chở hàng"
    assert not lists_products(result["reply"])


def test_purpose_variants_in_rule_mode(client):
    for index, text in enumerate(("Để phục vụ gia đình.", "Tôi dùng để đi làm hằng ngày.", "Đi lại trong thành phố")):
        conv = Conversation(client, f"variant{index}")
        conv.say(BUY)
        result = conv.say(text)
        assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
        assert _fact(conv.state, "intended_use")
        assert not lists_products(result["reply"])


def test_explicit_browse_is_stable_and_scoped(client):
    first = Conversation(client, "browse1").say("Cho tôi xem danh sách xe tải.")
    second = Conversation(client, "browse2").say("Tôi muốn xem tất cả các mẫu xe bên mình")
    assert "sales" in first["trace"] and "55 mẫu xe tải" in first["reply"]
    assert AMOUNT.findall(first["reply"]) == AMOUNT.findall(second["reply"]) != []
    assert unsupported_amounts(first["reply"]) == []


def test_browse_offer_accepted_with_yes(client):
    conv = Conversation(client, "vios")
    no_match = conv.say("Tôi muốn mua xe Toyota Vios")
    assert "chưa có mẫu" in no_match["reply"] and AMOUNT.findall(no_match["reply"]) == []
    assert any(item.kind.value == "OFFER" for item in conv.state.get("pending_interactions", []))
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
    assert len(product.search("Tôi muốn mua xe")) == 3


def test_requirements_persist_and_correct(client):
    conv = Conversation(client, "persist")
    conv.say(BUY)
    conv.say("Chở hàng")
    conv.say("Ngân sách 700 triệu")
    assert _fact(conv.state, "intended_use") == "Chở hàng"
    assert _fact(conv.state, "budget_vnd") == 700_000_000
    conv.say("Thực ra tôi cần xe phục vụ gia đình")
    assert _fact(conv.state, "intended_use") == "phục vụ gia đình"
    assert _fact(conv.state, "budget_vnd") == 700_000_000


def test_sessions_do_not_share_sales_state(client):
    a, b = Conversation(client, "iso-a"), Conversation(client, "iso-b")
    a.say(BUY)
    a.say("Chở hàng")
    assert not b.state.get("known_facts")
    result = b.say("Chở hàng")
    assert "sales" not in result["trace"] and not _fact(b.state, "intended_use")


def test_pending_question_does_not_hijack_other_turns(client):
    for index, text in enumerate(("Xin chào", "Thôi", "Phanh kêu là tại sao?", "Cảm ơn")):
        conv = Conversation(client, f"hijack{index}")
        conv.say(BUY)
        result = conv.say(text)
        assert "sales" not in result["trace"] and not _fact(conv.state, "intended_use")
        assert not any(item.kind.value == "QUESTION" and item.status.value == "OPEN"
                       for item in conv.state.get("pending_interactions", []))


def test_unsure_is_not_a_comprehension_failure(client):
    conv = Conversation(client, "unsure")
    conv.say(BUY)
    result = conv.say("Tôi chưa biết")
    assert "sales" in result["trace"] and NOT_UNDERSTOOD not in result["reply"].lower()
    assert not _fact(conv.state, "intended_use")


def test_sales_plus_showroom_visit_routes_both_tasks(client):
    conv = Conversation(client, "buy-drive")
    result = conv.say("Tôi muốn mua xe và đặt lịch xem xe tại showroom")
    assert {"sales", "appointment"} <= set(result["trace"])
    assert conv.state.get("active_workflow").workflow_type == "appointment"


def test_sales_question_keeps_booking_facts_of_active_workflow(client):
    conv = Conversation(client, "flow-keep")
    conv.say("Tôi muốn đặt lịch bảo dưỡng ngày mai")
    conv.say(BUY)
    assert _fact(conv.state, "service_type") == "MAINTENANCE"
    assert _fact(conv.state, "appointment_when") == "ngày mai"
