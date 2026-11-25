"""L6 (Đợt 4): hành trình E2E E2E01-E2E09 (kế hoạch, Phần D) chạy qua HTTP (/chat, /chat/{id}/messages, /admin/...)
+ graph thật + Postgres thật. LLM thay bằng FakeLLM, nên test xác định, không tốn tiền; việc LLM thật có hiểu đúng
tiếng Việt hay không là của L5.

Khác L3: mỗi test đi hết một hành trình nhiều lượt và kiểm trạng thái CUỐI ở cả ba nơi khách/nhân viên/DB nhìn thấy
(câu trả lời, tin poll, bảng appointments/slots/tickets), thay vì kiểm từng node.
Nếu test đỏ: đọc xem LỖI Ở TEST hay LỖI Ở CODE trước khi sửa.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from threading import Barrier

import pytest

from app.config import settings
from app.contracts.schemas import KnowledgeChunk, PolicySignals
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.identity import normalize_phone
from app.services import get_services
from app.services import booking
from app.services.booking import LOCAL_TZ
from tests.conversation_helpers import Conversation, unique_phone, unsupported_amounts
from tests.fake_llm import fake_llm, slot, task  # noqa: F401 - fixture

pytestmark = [pytest.mark.slow, pytest.mark.e2e]

MODEL = "FOTON AUMARK-9T (3800)"     # đúng một dòng khớp, giá 555.000.000 (xem test_inv_dependency)
PRICE_VND = 555_000_000
PRICE_TEXT = "555.000.000"
ACCEPT = task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER")
TOMORROW = date.today() + timedelta(days=1)


@pytest.fixture(autouse=True)
def clean_bookings():
    """Sức chứa khung giờ là dùng chung: dọn dữ liệu đặt lịch/hội thoại của test trước (không đụng catalog)."""
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


# ---------------------------------------------------------------------------------------------------------------
# Hàm dùng chung
# ---------------------------------------------------------------------------------------------------------------
def poll(client, conv: Conversation, after: int = 0) -> dict:
    response = client.get(f"/chat/{conv.id}/messages", params={"after": after})
    assert response.status_code == 200
    return response.json()


def slots_booked() -> dict[tuple, int]:
    """(loại slot, giờ bắt đầu theo giờ địa phương) -> số chỗ đã giữ."""
    with SessionLocal() as db:
        return {(s.slot_type, s.starts_at.astimezone(LOCAL_TZ)): s.booked_count for s in db.query(AppointmentSlot)}


def appointments_of(conv: Conversation) -> list[Appointment]:
    return conv.appointments()


def script_visit(fake_llm, text: str, *, with_price: bool, when: str | None, name_phone: tuple[str, str] | None):
    """Khai báo 'LLM hiểu gì' cho một tin xin xem xe (có thể kèm hỏi giá)."""
    tasks = []
    if with_price:
        tasks.append(task("SALES", "product_detail", "hỏi giá", car_model=MODEL))
    tasks.append(task("APPOINTMENT", "book_appointment", "xem xe"))
    slots = [slot("service_type", "SHOWROOM_VISIT", "xem xe"), slot("car_model", MODEL, MODEL)]
    if when:
        slots.append(slot("appointment_when", when, when))
    if name_phone:
        slots += [slot("customer_name", name_phone[0], name_phone[0]), slot("customer_phone", name_phone[1], name_phone[1])]
    fake_llm.understands(text, *tasks, slots=slots)


def answer_slots(fake_llm, text: str, *slots, domain="APPOINTMENT", action="book_appointment"):
    fake_llm.understands(text, task(domain, action, text, "ANSWER_TO_QUESTION"), slots=list(slots))


# ===== E2E01: hỏi giá -> xem xe -> nêu giờ -> họ tên + SĐT -> xác nhận =====
def test_e2e01_price_then_showroom_visit_confirmed_once(client, fake_llm):
    conv, phone = Conversation(client, "e2e01"), unique_phone()
    first_text = f"Giá xe {MODEL} bao nhiêu, mình muốn qua showroom xem xe"
    script_visit(fake_llm, first_text, with_price=True, when=None, name_phone=None)
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": MODEL})]],
                        final=f"{MODEL} có giá {PRICE_TEXT} đồng ạ.")
    first = conv.say(first_text)
    assert {"sales", "appointment"} <= set(first["trace"])
    assert PRICE_TEXT in first["reply"] and unsupported_amounts(first["reply"]) == []   # giá đúng catalog
    assert conv.appointments() == []

    when = "9 giờ sáng ngày mai"
    answer_slots(fake_llm, when, slot("appointment_when", when, when))
    conv.say(when)
    identity = f"Nguyễn Văn An, {phone}"
    answer_slots(fake_llm, identity, slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
                 slot("customer_phone", phone, phone))
    proposal = conv.say(identity)
    assert "xác nhận" in proposal["reply"].lower() and conv.appointments() == []        # mới đề xuất, chưa ghi DB

    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)
    last = conv.say("Đồng ý")
    [row] = appointments_of(conv)
    assert row.status == "CONFIRMED" and row.service_type == "SHOWROOM_VISIT"
    assert row.appointment_date == TOMORROW and row.start_time.hour == 9
    assert row.contact_phone == normalize_phone(phone) and conv.customer_id() == row.customer_id
    assert "đã đặt và xác nhận lịch hẹn" in last["reply"].lower()
    assert sum(slots_booked().values()) == 1                                           # đúng một chỗ được giữ

    fake_llm.understands("Cảm ơn", task("GREETING", "thanks", "Cảm ơn"))
    assert "appointment" not in conv.say("Cảm ơn")["trace"]
    assert len(appointments_of(conv)) == 1                                             # lời cảm ơn không ghi thêm


# ===== E2E01b: khách chưa nêu giờ cụ thể -> bot không ghi gì; nhánh PENDING_APPROVAL do nhân viên chốt giờ =====
def test_e2e01b_no_exact_time_bot_asks_and_writes_nothing(client, fake_llm):
    conv, phone = Conversation(client, "e2e01b"), unique_phone()
    text = f"Mình muốn qua showroom xem xe ngày mai, tên Nguyễn Văn An, SĐT {phone}"
    script_visit(fake_llm, text, with_price=False, when="ngày mai", name_phone=("Nguyễn Văn An", phone))
    result = conv.say(text)
    assert "giờ" in result["reply"].lower() and "xác nhận lịch hẹn" not in result["reply"].lower()
    assert conv.appointments() == [] and sum(slots_booked().values()) == 0               # chưa giữ chỗ nào


def test_e2e01b_staff_confirms_pending_visit_with_time_and_customer_is_notified(client, fake_llm):
    """Lịch chưa có giờ (PENDING_APPROVAL, tạo qua BookingService như khi bot/nhân viên nhận yêu cầu chỉ có ngày):
    nhân viên Confirm kèm giờ -> lịch CONFIRMED, giữ chỗ, và khách nhận thông báo trong chat (poll)."""
    from app.contracts.schemas import ServiceType
    from app.services.booking import DbBookingService
    conv = Conversation(client, "e2e01b-staff")
    created = DbBookingService().create_appointment(conv.id, ServiceType.SHOWROOM_VISIT, TOMORROW, None, unique_phone(),
                                                    "Xe: " + MODEL)
    assert created.ok and created.status == "PENDING_APPROVAL" and sum(slots_booked().values()) == 0
    needs_time = client.post(f"/admin/appointments/{created.appointment_id}/confirm")
    assert needs_time.status_code == 422                                                # chưa có giờ thì không xác nhận được
    ok = client.post(f"/admin/appointments/{created.appointment_id}/confirm", json={"start_time": "10:00"})
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED"
    [row] = appointments_of(conv)
    assert row.status == "CONFIRMED" and row.start_time.hour == 10 and sum(slots_booked().values()) == 1
    notices = [m["content"] for m in poll(client, conv)["messages"] if m["role"] == "system"]
    assert any(f"#{row.id}" in n and "đã được nhân viên xác nhận" in n for n in notices)


# ===== E2E02: hỏi lỗi kỹ thuật + đặt lịch trong cùng một tin =====
def test_e2e02_fault_question_and_booking_in_one_message(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_services().knowledge, "retrieve", lambda question, k=3: [
        KnowledgeChunk(text="Điều hòa không mát thường do thiếu gas hoặc bẩn dàn lạnh; nên kiểm tra sớm.",
                       source="test", score=1.0)])
    conv, phone = Conversation(client, "e2e02"), unique_phone()
    text = f"Điều hòa xe không mát là sao, đặt lịch kiểm tra ngày mai lúc 10 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "điều hòa không mát"),
                         task("APPOINTMENT", "book_appointment", "đặt lịch kiểm tra"),
                         slots=[slot("service_type", "REPAIR", "kiểm tra"),
                                slot("appointment_when", "ngày mai lúc 10 giờ sáng", "ngày mai lúc 10 giờ sáng"),
                                slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
                                slot("customer_phone", phone, phone)])
    result = conv.say(text)
    assert {"knowledge", "appointment"} <= set(result["trace"])
    assert "gas" in result["reply"] and "xác nhận" in result["reply"].lower()            # đủ cả hai ý trong một lượt
    assert conv.appointments() == []
    fake_llm.understands("Đúng rồi", ACCEPT, accepts_offer=True)
    conv.say("Đúng rồi")
    [row] = appointments_of(conv)
    assert row.status == "CONFIRMED" and row.service_type == "REPAIR"


def test_e2e02_safety_fault_gets_hotline_and_never_books(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_services().knowledge, "retrieve", lambda question, k=3: [
        KnowledgeChunk(text="Đèn áp suất dầu sáng: dừng xe ngay và không tiếp tục chạy.", source="test", score=1.0)])
    conv = Conversation(client, "e2e02-safety")
    text = "Đèn áp suất dầu sáng, mình qua xưởng luôn được không"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "đèn áp suất dầu"),
                         task("APPOINTMENT", "book_appointment", "qua xưởng"), signals=PolicySignals(safety_risk=True))
    fake_llm.uses_tools("policy", [[("flag_safety_risk", {})]])
    result = conv.say(text)
    assert "hotline" in result["reply"].lower() and "appointment" not in result["trace"]
    assert conv.appointments() == []


# ===== E2E03: giảm giá -> chuyển nhân viên =====
def test_e2e03_discount_request_goes_to_staff_without_commitment(client, fake_llm):
    conv = Conversation(client, "e2e03")
    text = "Giảm giá cho mình 10% nhé"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", "giảm giá 10%"),
                         signals=PolicySignals(discount_request=True))
    fake_llm.uses_tools("policy", [[("allow_bot", {})]])        # LLM 'lỡ' cho bot trả lời: code vẫn phải chặn
    result = conv.say(text)
    assert result["mode"] == "HUMAN_PENDING" and "human_handover" in result["trace"] and "sales" not in result["trace"]
    [ticket] = conv.tickets()
    assert "NEGOTIATION" in ticket.summary and ticket.status == "OPEN"                   # lý do ghi rõ
    reply = result["reply"].lower()
    assert "10%" not in reply and "giảm 10" not in reply and unsupported_amounts(result["reply"]) == []
    assert "số điện thoại" in reply                                                      # chưa có SĐT thì xin để nhân viên gọi lại


# ===== E2E04: đặt -> đổi -> hủy qua bot, slot trả/giữ đúng =====
def book_confirmed(client, fake_llm, conv: Conversation, phone: str, when: str = "ngày mai lúc 10 giờ sáng") -> Appointment:
    text = f"Mình muốn bảo dưỡng {when}, tên Nguyễn Văn An, SĐT {phone}"
    fake_llm.understands(text, task("APPOINTMENT", "book_appointment", "bảo dưỡng"),
                         slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng"), slot("appointment_when", when, when),
                                slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
                                slot("customer_phone", phone, phone)])
    conv.say(text)
    fake_llm.understands("Đúng rồi", ACCEPT, accepts_offer=True)
    conv.say("Đúng rồi")
    [row] = appointments_of(conv)
    assert row.status == "CONFIRMED"
    return row


def test_e2e04_book_reschedule_cancel_keeps_slots_consistent(client, fake_llm):
    conv, phone = Conversation(client, "e2e04"), unique_phone()
    row = book_confirmed(client, fake_llm, conv, phone)
    assert row.start_time.hour == 10 and sum(slots_booked().values()) == 1

    move = "Đổi lịch hẹn sang 2 giờ chiều ngày mai giúp mình"
    fake_llm.understands(move, task("APPOINTMENT", "reschedule_appointment", "đổi lịch"),
                         slots=[slot("appointment_when", "2 giờ chiều ngày mai", "2 giờ chiều ngày mai")])
    fake_llm.understands("Đồng ý đổi lịch", ACCEPT, accepts_offer=True)
    fake_llm.uses_tools("appointment", [
        [("get_appointment_details", {})],
        [("reschedule_appointment", lambda results: {"appointment_id": results[-1]["appointments"][0]["id"]})]],
        when="đổi lịch")
    proposal = conv.say(move)
    assert appointments_of(conv)[0].start_time.hour == 10 and "xác nhận" in proposal["reply"].lower()  # mới đề xuất
    moved = conv.say("Đồng ý đổi lịch")
    [row] = appointments_of(conv)
    assert row.status == "CONFIRMED" and row.start_time.hour == 14 and row.appointment_date == TOMORROW
    booked = slots_booked()
    assert sum(booked.values()) == 1                                                     # slot cũ đã trả, slot mới đã giữ
    assert [start.hour for (_, start), n in booked.items() if n] == [14]
    assert "đã đổi" in moved["reply"].lower()                                              # bot báo đổi vì DB đã đổi thật

    fake_llm.understands("Hủy lịch hẹn giúp mình", task("APPOINTMENT", "cancel_appointment", "hủy lịch hẹn"))
    fake_llm.understands("Hủy đi", ACCEPT, accepts_offer=True)
    fake_llm.uses_tools("appointment", [
        [("get_appointment_details", {})],
        [("cancel_appointment", lambda results: {"appointment_id": results[-1]["appointments"][0]["id"]})]],
        when="hủy")
    ask = conv.say("Hủy lịch hẹn giúp mình")
    assert appointments_of(conv)[0].status == "CONFIRMED" and "xác nhận" in ask["reply"].lower()
    done = conv.say("Hủy đi")
    assert appointments_of(conv)[0].status == "CANCELLED" and sum(slots_booked().values()) == 0
    assert "hủy" in done["reply"].lower() and "đã đặt và xác nhận" not in done["reply"].lower()


def test_e2e04_failed_reschedule_is_not_reported_as_done(client, fake_llm, monkeypatch):
    """Đổi sang khung đã đầy: DB không đổi và bot không báo 'đã đổi'."""
    monkeypatch.setattr("app.services.booking.settings", replace(settings, slot_capacity=1))
    other = Conversation(client, "e2e04-other")
    book_confirmed(client, fake_llm, other, unique_phone(), "ngày mai lúc 2 giờ chiều")
    conv = Conversation(client, "e2e04-fail")
    row = book_confirmed(client, fake_llm, conv, unique_phone())
    move = "Đổi lịch hẹn sang 2 giờ chiều ngày mai nhé"
    fake_llm.understands(move, task("APPOINTMENT", "reschedule_appointment", "đổi lịch"),
                         slots=[slot("appointment_when", "2 giờ chiều ngày mai", "2 giờ chiều ngày mai")])
    fake_llm.uses_tools("appointment", [
        [("get_appointment_details", {})],
        [("reschedule_appointment", lambda results: {"appointment_id": results[-1]["appointments"][0]["id"]})]],
        when="đổi lịch")
    reply = conv.say(move)["reply"].lower()
    [after] = appointments_of(conv)
    assert after.id == row.id and after.start_time.hour == 10                              # lịch cũ giữ nguyên
    assert "đã đổi" not in reply and "đã được đổi" not in reply


# ===== E2E05: nhân viên tiếp quản -> trả lời khách -> trả lại bot =====
def test_e2e05_staff_takeover_reply_and_return_to_bot(client, fake_llm):
    conv = Conversation(client, "e2e05")
    fake_llm.understands("Xin chào", task("GREETING", "greet", "Xin chào"))
    first = conv.say("Xin chào")
    assert first["mode"] == "BOT" and first["reply"]

    taken = client.post(f"/admin/conversations/{conv.id}/takeover")
    assert taken.status_code == 200
    ticket_id = taken.json()["ticket_id"]
    seen = poll(client, conv)
    assert seen["mode"] == "HUMAN_ACTIVE" and any("tiếp nhận" in m["content"].lower() or "tư vấn" in m["content"].lower()
                                                   for m in seen["messages"])

    calls_before = len(fake_llm.calls)
    silent = client.post("/chat", json={"session_id": conv.id, "message": "Mình cần hỏi về lịch hẹn"}).json()
    assert silent["reply"] == "" and silent["mode"] == "HUMAN_ACTIVE"                    # bot im lặng
    assert len(fake_llm.calls) == calls_before                                           # và không gọi LLM
    in_ticket = client.get(f"/admin/tasks/{ticket_id}/messages").json()
    assert any(m["role"] == "user" and "lịch hẹn" in m["content"] for m in in_ticket)    # tin khách vào ticket

    replied = client.post(f"/admin/tasks/{ticket_id}/reply", json={"content": "Chào anh, em xem lịch giúp anh nhé."})
    assert replied.status_code == 200
    assert any(m["role"] == "staff" and "xem lịch" in m["content"] for m in poll(client, conv)["messages"])

    resolved = client.post(f"/admin/tasks/{ticket_id}/resolve")
    assert resolved.status_code == 200 and poll(client, conv)["mode"] == "BOT"
    fake_llm.understands("Cảm ơn nhé", task("GREETING", "thanks", "Cảm ơn nhé"))
    back = conv.say("Cảm ơn nhé")
    assert back["mode"] == "BOT" and back["reply"]                                       # bot chạy lại sau khi được trả


# ===== E2E06: hỏi lịch sử sửa chữa (service stub) -> không bịa =====
def test_e2e06_repair_history_without_data_is_not_invented(client, fake_llm):
    conv = Conversation(client, "e2e06")
    text = "Lần trước xưởng làm gì cho xe mình, hết bao nhiêu tiền"
    fake_llm.understands(text, task("AFTER_SALES", "get_history", text))
    result = conv.say(text)
    assert "after_sales" in result["trace"]
    assert "chưa có dữ liệu" in result["reply"].lower() and unsupported_amounts(result["reply"]) == []
    assert conv.appointments() == [] and conv.tickets() == []


# ===== E2E07: hai khách tranh khung giờ cuối cùng cùng lúc =====
def test_e2e07_two_customers_race_for_the_last_slot(client, fake_llm, monkeypatch):
    monkeypatch.setattr("app.services.booking.settings", replace(settings, showroom_capacity=1))
    real_conflict = booking._conflict

    def slow_conflict(*args, **kwargs):
        result = real_conflict(*args, **kwargs)
        time.sleep(0.2)          # nới cửa sổ tranh chấp: nếu thiếu khóa ngày, cả hai khách sẽ cùng thấy 'còn chỗ'
        return result
    monkeypatch.setattr(booking, "_conflict", slow_conflict)
    when = "9 giờ sáng ngày mai"
    customers = []
    for tag in ("a", "b"):
        conv, phone = Conversation(client, f"e2e07{tag}"), unique_phone()
        text = f"Mình muốn xem xe tại showroom {when}, tên Nguyễn Văn An, SĐT {phone}"
        script_visit(fake_llm, text, with_price=False, when=when, name_phone=("Nguyễn Văn An", phone))
        proposal = conv.say(text)
        assert "xác nhận" in proposal["reply"].lower()                                    # cả hai đều thấy còn chỗ lúc đề xuất
        customers.append(conv)
    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)

    gate = Barrier(2)

    def accept(conv):
        gate.wait()
        return conv.say("Đồng ý")

    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(accept, customers))
    winners = [c for c in customers if any(a.status == "CONFIRMED" for a in appointments_of(c))]
    losers = [c for c in customers if c not in winners]
    assert len(winners) == 1 and len(losers) == 1                                        # chỉ một khách thành công
    assert sum(slots_booked().values()) == 1
    loser_reply = replies[customers.index(losers[0])]["reply"].lower()
    assert "đủ chỗ" in loser_reply or "còn trống" in loser_reply                          # báo đầy, gợi ý giờ khác
    assert "đã đặt và xác nhận" not in loser_reply and appointments_of(losers[0]) == []


# ===== E2E08: nhiều khách đồng thời và gửi dồn trong một phiên =====
def test_e2e08_concurrent_sessions_do_not_mix_state(client, fake_llm):
    hours = [8, 9, 10, 11, 8, 9]                                                          # mỗi khung tối đa 2 lịch
    plan = []
    for i, hour in enumerate(hours):
        conv, phone = Conversation(client, f"e2e08-{i}"), unique_phone()
        when = f"ngày mai lúc {hour} giờ sáng"
        text = f"Mình muốn bảo dưỡng {when}, tên Nguyễn Văn An{i}, SĐT {phone} (khách {i})"
        fake_llm.understands(text, task("APPOINTMENT", "book_appointment", "bảo dưỡng"),
                             slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng"), slot("appointment_when", when, when),
                                    slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
                                    slot("customer_phone", phone, phone)])
        plan.append((conv, phone, hour, text))
    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)

    with ThreadPoolExecutor(len(plan)) as pool:
        proposals = list(pool.map(lambda p: p[0].say(p[3]), plan))
    assert all("xác nhận" in r["reply"].lower() for r in proposals)
    assert [r["session_id"] for r in proposals] == [p[0].id for p in plan]                # đúng phiên trả lời đúng phiên
    with ThreadPoolExecutor(len(plan)) as pool:
        list(pool.map(lambda p: p[0].say("Đồng ý"), plan))
    for conv, phone, hour, _ in plan:
        [row] = appointments_of(conv)
        assert row.status == "CONFIRMED" and row.start_time.hour == hour and row.contact_phone == normalize_phone(phone)
    assert sum(slots_booked().values()) == len(plan)


def test_e2e08_burst_of_accepts_in_one_session_books_once(client, fake_llm):
    conv, phone = Conversation(client, "e2e08-burst"), unique_phone()
    text = f"Mình muốn bảo dưỡng ngày mai lúc 10 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    fake_llm.understands(text, task("APPOINTMENT", "book_appointment", "bảo dưỡng"),
                         slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng"),
                                slot("appointment_when", "ngày mai lúc 10 giờ sáng", "ngày mai lúc 10 giờ sáng"),
                                slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
                                slot("customer_phone", phone, phone)])
    conv.say(text)
    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)
    with ThreadPoolExecutor(4) as pool:
        responses = list(pool.map(lambda _: client.post("/chat", json={"session_id": conv.id, "message": "Đồng ý"}),
                                  range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert len(appointments_of(conv)) == 1 and sum(slots_booked().values()) == 1        # không tạo hai lịch
    assert len(conv.tickets()) <= 1


# ===== E2E09 (P1): chốt mua -> đủ 5 trường -> hẹn ngày ký =====
def deal_text(phone: str, price: int | None = PRICE_VND) -> tuple[str, list]:
    price_text = f", giá chốt {price}" if price is not None else ""
    text = f"Mình chốt mua {MODEL}, tên Nguyễn Văn An, SĐT {phone}, địa chỉ 12 Lê Lợi Hà Nội{price_text}"
    slots = [slot("car_model", MODEL, MODEL), slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
             slot("customer_phone", phone, phone), slot("customer_address", "12 Lê Lợi Hà Nội", "12 Lê Lợi Hà Nội")]
    if price is not None:
        slots.append(slot("decided_price", price, str(price)))
    return text, slots


def test_e2e09_contract_collected_then_signing_date_booked(client, fake_llm):
    conv, phone = Conversation(client, "e2e09"), unique_phone()
    text, slots = deal_text(phone)
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua"), slots=slots)
    first = conv.say(text)
    assert "contract" in first["trace"] and "human_handover" not in first["trace"]
    workflow = conv.state["active_workflow"]
    assert workflow.workflow_type == "appointment"                                       # chuyển sang hẹn ngày ký
    assert conv.appointments() == [] and "ký" in first["reply"].lower()

    when = "9 giờ sáng ngày mai"
    answer_slots(fake_llm, when, slot("appointment_when", when, when))
    proposal = conv.say(when)
    assert "xác nhận" in proposal["reply"].lower() and conv.appointments() == []

    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)
    conv.say("Đồng ý")
    [row] = appointments_of(conv)
    assert row.service_type == "CONTRACT_SIGN" and row.status == "CONFIRMED"
    assert row.appointment_date == TOMORROW and row.start_time.hour == 9
    assert MODEL in (row.note or "")                                                     # xe chốt mua nằm trong ghi chú lịch ký


def test_e2e09_price_change_after_contract_goes_to_staff(client, fake_llm):
    conv, phone = Conversation(client, "e2e09-change"), unique_phone()
    text, slots = deal_text(phone)
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua"), slots=slots)
    conv.say(text)
    change = "Thôi cho mình đổi giá chốt xuống 500 triệu"
    fake_llm.understands(change, task("CONTRACT", "start_contract", change, "ANSWER_TO_QUESTION"),
                         slots=[slot("decided_price", 500_000_000, "500 triệu")])
    result = conv.say(change)
    assert "human_handover" in result["trace"] and result["mode"] == "HUMAN_PENDING"
    assert any(reason in t.summary for t in conv.tickets()
               for reason in ("CONTRACT_OR_PRICE_CHANGE", "CONTRACT_PRICE_MISMATCH"))
    assert "500" not in result["reply"] and conv.appointments() == []                    # bot không cam kết giá mới
