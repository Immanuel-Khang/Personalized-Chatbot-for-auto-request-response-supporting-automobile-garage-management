"""L3 (Đợt 3B): giá chốt hợp đồng được đối chiếu với catalog (contract._catalog_price), R04.

Giá khác catalog hoặc không xác minh được -> chuyển nhân viên (CONTRACT_PRICE_MISMATCH / CONTRACT_PRICE_UNVERIFIED),
giá đề xuất KHÔNG được ghi vào known_facts/hợp đồng, câu trả lời không nhắc lại con số. ID: CP01-CP08.
"""
import pytest

from app.agent.nodes.contract import _catalog_price
from app.contracts.schemas import PolicySignals
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services
from tests.conversation_helpers import Conversation, unique_phone
from tests.fake_llm import fake_llm, slot, task  # noqa: F401 - fixture

MODEL, PRICE = "FOTON AUMARK-9T (3800)", 555_000_000


@pytest.fixture(autouse=True)
def clean_bookings():
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


def deal(fake_llm, *, model: str = MODEL, price: int | None = PRICE, signals: PolicySignals | None = None) -> str:
    phone = unique_phone()
    price_text = f", giá chốt {price}" if price is not None else ""
    text = f"Mình chốt mua {model}, tên Nguyễn Văn An, SĐT {phone}, địa chỉ 12 Lê Lợi Hà Nội{price_text}"
    slots = [slot("car_model", model, model), slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
             slot("customer_phone", phone, phone), slot("customer_address", "12 Lê Lợi Hà Nội", "12 Lê Lợi Hà Nội")]
    if price is not None:
        slots.append(slot("decided_price", price, str(price)))
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua"), slots=slots,
                         signals=signals or PolicySignals())
    return text


def reasons(conv: Conversation) -> str:
    return " ".join(t.summary or "" for t in conv.tickets())


# ===== _catalog_price (đơn vị) =====
def test_cp01_catalog_price_needs_an_exact_single_model_match(client):
    assert _catalog_price(MODEL) == PRICE
    assert _catalog_price(MODEL.lower()) == PRICE                    # so khớp không phân biệt hoa thường
    assert _catalog_price("FOTON AUMARK-9T") is None                 # chỉ khớp một phần (nhiều mẫu) -> không đoán
    assert _catalog_price("Tesla Cybertruck") is None and _catalog_price(None) is None and _catalog_price("") is None


def test_cp02_catalog_price_lookup_error_is_treated_as_unverified(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(get_services().product, "find_model", broken)
    assert _catalog_price(MODEL) is None


# ===== Luồng /chat =====
def test_cp03_price_equal_to_catalog_continues_the_contract(client, fake_llm):
    conv = Conversation(client, "cp03")
    result = conv.say(deal(fake_llm))
    assert "contract" in result["trace"] and "human_handover" not in result["trace"]
    assert conv.tickets() == []
    assert conv.state["known_facts"]["decided_price"].value == PRICE


def test_cp04_price_different_from_catalog_goes_to_staff_and_is_not_recorded(client, fake_llm):
    conv = Conversation(client, "cp04")
    result = conv.say(deal(fake_llm, price=480_000_000))
    assert "human_handover" in result["trace"]
    assert "CONTRACT_PRICE_MISMATCH" in reasons(conv)
    assert "480" not in result["reply"] and "555" not in result["reply"]          # không nhắc lại số tiền nào
    assert "decided_price" not in conv.state["known_facts"]                        # giá lệch không thành điều khoản
    assert conv.state["active_workflow"].status.value == "WAITING_FOR_APPROVAL"
    assert conv.appointments() == []


def test_cp05_model_not_in_catalog_cannot_verify_the_price(client, fake_llm):
    conv = Conversation(client, "cp05")
    result = conv.say(deal(fake_llm, model="Tesla Cybertruck", price=555_000_000))
    assert "human_handover" in result["trace"] and "CONTRACT_PRICE_UNVERIFIED" in reasons(conv)
    assert "decided_price" not in conv.state["known_facts"]


def test_cp06_ambiguous_partial_model_name_cannot_verify_the_price(client, fake_llm):
    conv = Conversation(client, "cp06")
    result = conv.say(deal(fake_llm, model="FOTON AUMARK-9T", price=555_000_000))
    assert "human_handover" in result["trace"] and "CONTRACT_PRICE_UNVERIFIED" in reasons(conv)


def test_cp07_no_price_yet_asks_for_it_instead_of_handing_over(client, fake_llm):
    conv = Conversation(client, "cp07")
    result = conv.say(deal(fake_llm, price=None))
    assert "contract" in result["trace"] and "human_handover" not in result["trace"]
    assert "giá chốt" in result["reply"].lower() and conv.tickets() == []


def test_cp08_price_renegotiation_signal_still_goes_to_staff_without_keeping_the_price(client, fake_llm):
    conv = Conversation(client, "cp08")
    result = conv.say(deal(fake_llm, price=500_000_000, signals=PolicySignals(deal_change=True)))
    assert "human_handover" in result["trace"] and "CONTRACT_OR_PRICE_CHANGE" in reasons(conv)
    assert "decided_price" not in conv.state["known_facts"]
