"""L3: bất biến orchestration OI01-OI11 (kế hoạch B5) chạy qua /chat + graph thật + Postgres, LLM thay bằng FakeLLM.

Mỗi test dựng kịch bản bằng fake_llm (hiểu gì, gọi công cụ nào) rồi assert trên trace, DB và câu trả lời.
Các kịch bản này đã được chạy thử trên graph thật với dịch vụ giả trong bộ nhớ; phần DB (appointments, tickets)
cần chạy trên garage_test của bạn. Nếu test nào đỏ: đọc kỹ xem LỖI Ở TEST hay LỖI Ở CODE trước khi sửa.

Ghi chú về Conversation.say(): helper này assert 'mỗi node một lần trong trace'. Lượt có validation thử lại
(OI11) đi qua preprocess hai lần nên test đó gọi client.post trực tiếp.
"""
import re

import pytest

from app.contracts.schemas import KnowledgeChunk, PolicySignals
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services
from tests.conversation_helpers import Conversation, defect, unique_phone
from tests.fake_llm import fake_llm, slot, task, tool_call_spy  # noqa: F401 - hai fixture

HOTLINE = "hotline"


@pytest.fixture(autouse=True)
def clean_bookings():
    """Lịch/ticket của test trước không được chiếm sức chứa khung giờ 'ngày mai 10h' của test sau (mặc định 2 lịch/khung).
    Chỉ xóa dữ liệu đặt lịch và hội thoại, KHÔNG đụng catalog."""
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


@pytest.fixture()
def rag_chunk(monkeypatch):
    """RAG trả đúng một đoạn tài liệu cố định: L3 không đo chất lượng truy xuất (đó là L4)."""
    def set_chunk(text: str) -> None:
        monkeypatch.setattr(get_services().knowledge, "retrieve",
                            lambda question, k=3: [KnowledgeChunk(text=text, source="test", score=1.0)])
    return set_chunk


def booking_message(phone: str) -> str:
    return f"Mình muốn bảo dưỡng ngày mai lúc 10 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"


def script_booking_request(fake_llm, phone: str, *, with_phone: bool = True) -> str:
    """Khách nêu đủ dịch vụ, ngày giờ, họ tên (và SĐT nếu with_phone) trong một tin."""
    text = booking_message(phone) if with_phone else "Mình muốn bảo dưỡng ngày mai lúc 10 giờ sáng, tên Nguyễn Văn An"
    slots = [slot("service_type", "MAINTENANCE", "bảo dưỡng"),
             slot("appointment_when", "ngày mai lúc 10 giờ sáng", "ngày mai lúc 10 giờ sáng"),
             slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An")]
    if with_phone:
        slots.append(slot("customer_phone", phone, phone))
    fake_llm.understands(text, task("APPOINTMENT", "book_appointment", "bảo dưỡng"), slots=slots)
    return text


# ===== Ví dụ chuyển đổi một test cũ (TC05 'known phone books once') sang FakeLLM + luồng hiện tại =====
def test_booking_proposes_first_then_writes_exactly_once_after_confirmation(client, fake_llm):
    """Luồng thật: đủ 4 thông tin -> bot ĐỀ XUẤT (chưa ghi) -> khách xác nhận ở tin kế -> ghi đúng một lịch CONFIRMED."""
    phone = unique_phone()
    conv = Conversation(client, "oi-booking")
    first = conv.say(script_booking_request(fake_llm, phone))
    assert "appointment" in first["trace"] and conv.appointments() == []          # OI04: chưa xác nhận thì chưa ghi DB
    assert "xác nhận" in first["reply"]

    fake_llm.understands("Đúng rồi", task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER"),
                         accepts_offer=True)
    second = conv.say("Đúng rồi")
    [row] = conv.appointments()
    assert row.status == "CONFIRMED" and row.service_type == "MAINTENANCE" and row.start_time.hour == 10
    assert row.contact_phone == "+84" + phone[1:]
    assert fake_llm.tool_names("appointment") == ["create_appointment", "create_appointment"]  # lượt 1 đề xuất, lượt 2 ghi

    fake_llm.understands("Cảm ơn bạn", task("GREETING", "greeting", "Cảm ơn bạn"))
    conv.say("Cảm ơn bạn")
    assert len(conv.appointments()) == 1                                           # lượt sau không ghi thêm


# ===== OI01: nhiều agent chỉ đọc chạy trong cùng một lượt =====
def test_oi01_parallel_read_agents_in_one_turn(client, fake_llm, rag_chunk):
    rag_chunk("Phanh kêu thường do má phanh mòn; nên kiểm tra sớm.")
    text = "Xe Foton Aumark có những phiên bản nào, còn phanh kêu là tại sao"
    fake_llm.understands(text, task("SALES", "search_catalog", "phiên bản Foton Aumark"),
                         task("KNOWLEDGE", "answer_knowledge", "phanh kêu"))
    fake_llm.uses_tools("sales", [], final="Foton Aumark có nhiều phiên bản, mình sẽ gửi chi tiết khi bạn cần ạ.")
    result = Conversation(client, "oi01").say(text)
    assert {"coordinator", "sales", "knowledge", "compose"} <= set(result["trace"])
    assert "Aumark" in result["reply"] and "má phanh" in result["reply"]            # đủ cả hai ý


# ===== OI02/OI03: tối đa một writer mỗi lượt; writer thứ hai được hoãn, không mất =====
def test_oi03_second_writer_is_deferred_and_resumes_next_turn(client, fake_llm):
    conv = Conversation(client, "oi03")  # say() đã assert OI02 mỗi lượt
    text = "Mình chốt mua xe, đặt lịch qua showroom xem xe nữa"
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua xe"),
                         task("APPOINTMENT", "book_appointment", "đặt lịch showroom"))
    first = conv.say(text)
    assert "contract" in first["trace"] and "appointment" not in first["trace"]
    assert "đặt lịch hẹn" in first["reply"]                                         # ghi chú hoãn (DEFERRED_NOTE)

    fake_llm.understands("Được", task("OTHER", "general_conversation", "Được", "ACCEPT_OFFER"), accepts_offer=True)
    second = conv.say("Được")
    assert "appointment" in second["trace"] and "contract" not in second["trace"]


# ===== OI04: writer không ghi DB khi thiếu thông tin =====
def test_oi04_missing_phone_asks_and_writes_nothing(client, fake_llm):
    conv = Conversation(client, "oi04")
    result = conv.say(script_booking_request(fake_llm, unique_phone(), with_phone=False))
    assert "số điện thoại" in result["reply"].lower() and conv.appointments() == []


# ===== OI05: không báo hoàn thành giả =====
def test_oi05_unconfirmed_booking_claim_is_replaced_by_handover(client, fake_llm):
    conv = Conversation(client, "oi05")
    text = script_booking_request(fake_llm, unique_phone())
    # LLM 'nói quá': câu trả lời khẳng định đã đặt lịch trong khi công cụ mới chỉ NEEDS_CONFIRMATION
    fake_llm.uses_tools("appointment", [[("create_appointment", {})]], final="Mình đã đặt lịch hẹn cho bạn rồi nhé!")
    result = conv.say(text)
    assert "human_handover" in result["trace"] and "đã đặt lịch" not in result["reply"].lower()
    assert conv.appointments() == []
    assert "VALIDATION: unconfirmed action claim" in conv.tickets()[-1].summary


# ===== OI06: tin an toàn chỉ chạy knowledge, kèm hotline =====
def test_oi06_safety_message_runs_knowledge_alone_with_hotline(client, fake_llm, rag_chunk):
    rag_chunk("Đèn áp suất dầu sáng: dừng xe ngay và không tiếp tục chạy.")
    text = "Đèn áp suất dầu sáng, mình qua xưởng luôn được không"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "đèn áp suất dầu"),
                         task("APPOINTMENT", "book_appointment", "qua xưởng"),
                         signals=PolicySignals(safety_risk=True))
    fake_llm.uses_tools("policy", [[("flag_safety_risk", {})]])
    result = Conversation(client, "oi06").say(text)
    assert "knowledge" in result["trace"] and not {"sales", "appointment", "coordinator"} & set(result["trace"])
    assert HOTLINE in result["reply"]


# ===== OI07: Output Guard chặn số tiền lạ, cho qua số tiền có nguồn =====
MAINTENANCE_TEXT = "Xe chạy 10.000 km thì bảo dưỡng gì, chi phí bao nhiêu"


def script_maintenance_question(fake_llm) -> None:
    fake_llm.understands(MAINTENANCE_TEXT, task("MAINTENANCE", "maintenance_schedule", "bảo dưỡng 10.000 km"),
                         slots=[slot("current_mileage_km", 10000, "10.000 km")])


def test_oi07_amount_not_from_tools_is_blocked_and_handed_over(client, fake_llm, rag_chunk):
    rag_chunk("Mốc 10.000 km: thay dầu và lọc dầu.")
    script_maintenance_question(fake_llm)
    fake_llm.says("knowledge", "Chi phí khoảng 150.000.000 đồng.")                  # >= 9 chữ số, không có trong dữ liệu
    conv = Conversation(client, "oi07-bad")
    result = conv.say(MAINTENANCE_TEXT)
    assert "human_handover" in result["trace"] and "150.000.000" not in result["reply"]
    assert "OUTPUT_GUARD: số tiền lạ" in conv.tickets()[-1].summary


def test_oi07_amount_from_the_maintenance_tool_passes(client, fake_llm, rag_chunk):
    rag_chunk("Mốc 10.000 km: thay dầu và lọc dầu.")
    script_maintenance_question(fake_llm)
    fake_llm.says("knowledge", "Chi phí ước tính khoảng 1.500.000 đồng.")           # đúng COST_MAP[10000]
    result = Conversation(client, "oi07-ok").say(MAINTENANCE_TEXT)
    assert "human_handover" not in result["trace"] and "1.500.000" in result["reply"]


# ===== OI08: writer đã ghi DB nhưng Output Guard bỏ cả bản nháp (hành vi quan sát được, xem DEFECT-14) =====
@defect("DEFECT-14", "writer đã ghi lịch nhưng guard bỏ cả bản nháp nên khách chỉ nhận lời chuyển nhân viên, "
                     "không biết lịch đã được đặt")
def test_oi08_customer_is_told_the_booking_succeeded_even_if_another_part_is_blocked(client, fake_llm, rag_chunk):
    rag_chunk("Mốc 10.000 km: thay dầu và lọc dầu.")
    conv = Conversation(client, "oi08")
    conv.say(script_booking_request(fake_llm, unique_phone()))
    text = "Đúng rồi, à xe chạy 10.000 km thì bảo dưỡng gì, chi phí bao nhiêu"
    fake_llm.understands(text, task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER"),
                         task("MAINTENANCE", "maintenance_schedule", "chi phí 10.000 km"), accepts_offer=True,
                         slots=[slot("current_mileage_km", 10000, "10.000 km")])
    fake_llm.says("knowledge", "Chi phí khoảng 150.000.000 đồng.")
    result = conv.say(text)
    assert len(conv.appointments()) == 1 and "human_handover" in result["trace"]   # đúng với code hiện tại
    assert re.search(r"đã (đặt|xác nhận)", result["reply"].lower())                # mong muốn: khách được báo


# ===== OI10: quy tắc cứng của policy guard nằm trong code, LLM nói allow_bot cũng không đổi =====
def test_oi10_discount_request_goes_to_human_even_if_llm_says_allow_bot(client, fake_llm):
    text = "Giảm giá cho mình 10% nhé"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", "giảm giá 10%"),
                         signals=PolicySignals(discount_request=True))
    fake_llm.uses_tools("policy", [[("allow_bot", {})]])
    conv = Conversation(client, "oi10-discount")
    result = conv.say(text)
    assert result["mode"] == "HUMAN_PENDING" and "human_handover" in result["trace"] and "sales" not in result["trace"]
    assert "NEGOTIATION" in conv.tickets()[-1].summary


def test_oi10_explicit_human_request_goes_to_human_without_asking_the_llm(client, fake_llm, tool_call_spy):
    text = "Cho mình gặp nhân viên"
    fake_llm.understands(text, task("OTHER", "general_conversation", "gặp nhân viên"),
                         signals=PolicySignals(wants_human=True))
    conv = Conversation(client, "oi10-human")
    result = conv.say(text)
    assert "human_handover" in result["trace"] and "router" not in result["trace"]
    assert [call.node for call in tool_call_spy if call.node == "policy_guard"] == []  # không cần phán đoán của LLM
    assert "HUMAN_REQUEST" in conv.tickets()[-1].summary


# ===== OI11: validation thử lại đúng một lần =====
def post(client, session_id: str, text: str) -> dict:
    response = client.post("/chat", json={"session_id": session_id, "message": text})
    assert response.status_code == 200
    return response.json()


def test_oi11_empty_specialist_result_retries_preprocess_exactly_once(client, fake_llm):
    text = "Cho mình hỏi giá xe Foton"
    fake_llm.understands(text, task("SALES", "product_specs", "giá xe Foton"))
    # sales không gọi công cụ và trả chuỗi rỗng -> không agent nào có kết quả
    fake_llm.uses_tools("sales", [], final="")
    result = post(client, "oi11-empty-0001", text)
    assert result["trace"].count("preprocess") == 2 and result["trace"].count("validation") == 2


@defect("DEFECT-13", "sau lần thử lại mà specialist vẫn trả rỗng, không có needs_human nên khách nhận tin rỗng")
def test_oi11_customer_never_receives_an_empty_reply(client, fake_llm):
    text = "Cho mình hỏi giá xe Foton"
    fake_llm.understands(text, task("SALES", "product_specs", "giá xe Foton"))
    fake_llm.uses_tools("sales", [], final="")
    result = post(client, "oi11-empty-0002", text)
    assert result["reply"].strip() or "human_handover" in result["trace"]
