"""Smoke test: bảo đảm khung sườn chạy được đầu-cuối. Mỗi tính năng mới nên thêm 1 test ở đây hoặc file riêng.

Chạy bằng FakeLLM (tests/fake_llm.py): kịch bản khai báo 'LLM hiểu gì / gọi công cụ nào', phần còn lại là code thật
(graph, router, policy guard, ghi DB, admin API). Không kiểm bot có hiểu tiếng Việt hay không (đó là L5).
Khác thiết kế cũ: HUMAN_PENDING KHÔNG còn im lặng. Bot vẫn chạy graph và trả lời; chỉ HUMAN_ACTIVE là im lặng.
"""
import uuid

import pytest

from app.contracts.schemas import PolicySignals
from app.db.models import HandoverTicket, Manager, Message
from app.db.session import SessionLocal
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

pytestmark = pytest.mark.usefixtures("clean_bookings")  # khung giờ chỉ chứa 2 lịch: dọn lịch/ticket/tin nhắn trước mỗi test

PRICE_ANSWER = "Foton Aumark có giá khoảng 500.000.000 đồng ạ."
SLOT_ASK = "xin ngày giờ hẹn"  # câu bot hỏi tiếp ngày giờ (app/agent/nodes/appointment.py)
PHONE = "0912345678"


def chat(client, text, token="t1", conv=None):
    r = client.post("/chat", json={"session_id": conv or f"{token}-{uuid.uuid4().hex}", "message": text})
    assert r.status_code == 200
    return r.json()


def _appt(excerpt, relationship="NEW_REQUEST"):
    return task("APPOINTMENT", "book_appointment", excerpt, relationship)


def _tasks(conv_id):
    with SessionLocal() as db:
        return db.query(HandoverTicket).filter_by(session_id=conv_id).order_by(HandoverTicket.id).all()


def _poll(client, conv_id, token, after=0):
    r = client.get(f"/chat/{conv_id}/messages", params={"after": after})
    assert r.status_code == 200
    return r.json()


def _hand_over(fake_llm, text="Mình muốn gặp nhân viên"):
    fake_llm.understands(text, task("OTHER", "request_human", text), signals=PolicySignals(wants_human=True))
    return text


# ===== Hội thoại cơ bản =====
def test_greeting(client, fake_llm):
    fake_llm.understands("Xin chào", task("GREETING", "greet", "Xin chào"))
    fake_llm.says("conversation", "Chào bạn, mình là trợ lý của xưởng Foton ạ.")
    r = chat(client, "Xin chào")
    assert "conversation" in r["trace"] and "trợ lý" in r["reply"].lower()


def test_sales_shows_price_with_date(client, fake_llm):
    text = "Cho mình hỏi giá xe Foton Aumark"
    fake_llm.understands(text, task("SALES", "ask_price", text))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    r = chat(client, text)
    assert "sales" in r["trace"] and "500.000.000 đồng" in r["reply"]


def test_negotiation_goes_to_human(client, fake_llm):
    """Giảm giá (không có car_model rõ ràng) → chuyển người, bằng code, không cần LLM quyết."""
    text = "Giảm giá cho mình 10% nhé"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", text), signals=PolicySignals(discount_request=True))
    r = chat(client, text, token="t2")
    assert r["mode"] == "HUMAN_PENDING" and "human_handover" in r["trace"]


def test_discount_with_car_model_goes_to_human(client, fake_llm):
    """Không còn auto-approve: kể cả nêu rõ xe + % nhỏ vẫn chuyển người."""
    text = "Xe Foton Aumark giảm giá cho mình 3% nhé"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", text), signals=PolicySignals(discount_request=True),
                         slots=[slot("car_model", "Foton Aumark")])
    r = chat(client, text, token="t8")
    assert r["mode"] == "HUMAN_PENDING" and "human_handover" in r["trace"] and "sales" not in r["trace"]


# ===== HUMAN_PENDING: bot vẫn trả lời, không tạo ticket thứ hai =====
def test_pending_bot_still_answers_and_keeps_single_ticket(client, fake_llm):
    """(Test cũ 'pending_bot_silent' lỗi thời.) Đang chờ nhân viên: bot vẫn chạy graph; tin khách vào ticket mở."""
    first = "Giảm giá cho mình đi"
    fake_llm.understands(first, task("DISCOUNT", "request_discount", first), signals=PolicySignals(discount_request=True))
    r = chat(client, first, token="t5")
    assert r["mode"] == "HUMAN_PENDING"
    question = "Cho mình hỏi giá xe Foton Aumark"
    fake_llm.understands(question, task("SALES", "ask_price", question))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    r2 = chat(client, question, token="t5", conv=r["session_id"])
    assert r2["mode"] == "HUMAN_PENDING" and "sales" in r2["trace"] and "500.000.000 đồng" in r2["reply"]
    tickets = _tasks(r["session_id"])
    assert len(tickets) == 1 and question in tickets[0].summary  # tin khách được ghi thêm vào ticket đang mở


def test_pending_extra_human_request_appends_ticket(client, fake_llm):
    """Đang chờ mà khách lại mặc cả/khiếu nại → ghi thêm vào ticket đang mở, không mở ticket thứ 2."""
    first = "Giảm giá cho mình đi"
    fake_llm.understands(first, task("DISCOUNT", "request_discount", first), signals=PolicySignals(discount_request=True))
    r = chat(client, first, token="t16")
    second = "Bớt thêm chút nữa được không, mình khiếu nại đấy"
    fake_llm.understands(second, task("DISCOUNT", "request_discount", second),
                         signals=PolicySignals(discount_request=True, complaint=True))
    fake_llm.uses_tools("policy", [[("handover_to_human", {"reason": "COMPLAINT"})]])
    r2 = chat(client, second, token="t16", conv=r["session_id"])
    assert "human_handover" in r2["trace"] and r2["mode"] == "HUMAN_PENDING"
    assert "khiếu nại" in _tasks(r["session_id"])[-1].summary
    assert len(_tasks(r["session_id"])) == 1


def test_pending_saves_phone_to_ticket(client, fake_llm):
    text = _hand_over(fake_llm)
    r = chat(client, text, token="t13")
    fake_llm.understands("0987654321", task("OTHER", "provide_info", "0987654321", "ANSWER_TO_QUESTION"),
                         slots=[slot("customer_phone", "0987654321")])
    chat(client, "0987654321", token="t13", conv=r["session_id"])
    assert "0987654321" in _tasks(r["session_id"])[-1].summary


def test_pending_timeout_sends_hotline_once(client, fake_llm):
    """Quá thời hạn chờ → thông báo system (poll được) chỉ gửi 1 lần cho mỗi ticket. Ở HUMAN_PENDING bot vẫn trả lời
    (khác test cũ), nên không còn kiểm 'reply rỗng'."""
    from datetime import timedelta
    text = "Mình muốn khiếu nại"
    fake_llm.understands(text, task("OTHER", "complain", text), signals=PolicySignals(complaint=True))
    fake_llm.uses_tools("policy", [[("handover_to_human", {"reason": "COMPLAINT"})]])
    r = chat(client, text, token="t14")
    with SessionLocal() as db:
        ticket = db.get(HandoverTicket, _tasks(r["session_id"])[-1].id)
        ticket.sla_due_at -= timedelta(hours=1)
        db.commit()
    fake_llm.understands("Xin chào", task("GREETING", "greet", "Xin chào"))
    for _ in range(2):  # hai lượt sau khi quá hạn: thông báo vẫn chỉ xuất hiện một lần
        r2 = chat(client, "Xin chào", token="t14", conv=r["session_id"])
        assert r2["mode"] == "HUMAN_PENDING"
    notices = [m for m in _poll(client, r["session_id"], "t14")["messages"]
               if m["role"] == "system" and "hotline" in m["content"]]
    assert len(notices) == 1


# ===== Đặt lịch nhiều lượt =====
def test_appointment_slot_filling_multi_turn(client, fake_llm):
    """Luồng hiện tại: hỏi từng phần còn thiếu → ĐỀ XUẤT → ghi lịch ở lượt khách xác nhận (không còn 'ghi nhận lịch hẹn'
    ngay sau khi có SĐT)."""
    start = "Mình muốn đặt lịch bảo dưỡng"
    fake_llm.understands(start, _appt("đặt lịch bảo dưỡng"), slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng")])
    r = chat(client, start, token="t3")
    conv = r["session_id"]
    when = "9 giờ sáng thứ 7"
    fake_llm.understands(when, _appt(when, "ANSWER_TO_QUESTION"), slots=[slot("appointment_when", when, when)])
    r = chat(client, when, token="t3", conv=conv)
    assert "họ và tên" in r["reply"].lower()  # bot đòi họ tên đầy đủ trước khi hỏi tiếp
    identity = f"Nguyễn Văn An {PHONE}"
    fake_llm.understands(identity, _appt(identity, "ANSWER_TO_QUESTION"),
                         slots=[slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", PHONE)])
    r = chat(client, identity, token="t3", conv=conv)
    assert "xác nhận" in r["reply"].lower()
    with SessionLocal() as db:
        assert db.query(Message).filter_by(session_id=conv, role="ai").count() == 3
    fake_llm.understands("Đồng ý", _appt("đồng ý", "ACCEPT_OFFER"), accepts_offer=True)
    fake_llm.uses_tools("appointment", [[("create_appointment", {})]])
    r = chat(client, "Đồng ý", token="t3", conv=conv)
    assert "đã đặt và xác nhận lịch hẹn" in r["reply"].lower()


def test_sales_and_appointment_same_turn(client, fake_llm):
    """Giá + xem xe tại showroom: sales báo giá và appointment hỏi slot trong cùng một lượt, câu hỏi slot nằm cuối."""
    text = "Giá xe Foton Aumark bao nhiêu, cho mình qua showroom xem xe"
    fake_llm.understands(text, task("SALES", "ask_price", "Giá xe Foton Aumark bao nhiêu"),
                         _appt("cho mình qua showroom xem xe"), slots=[slot("service_type", "SHOWROOM_VISIT", "showroom")])
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    r = chat(client, text, token="t11")
    assert {"sales", "appointment"} <= set(r["trace"])
    assert r["trace"][-3:] == ["compose", "output_guard", "respond"]
    assert "đồng" in r["reply"] and r["reply"].rstrip().endswith("nhé.") and SLOT_ASK in r["reply"]
    assert r["reply"].index("đồng") < r["reply"].index(SLOT_ASK)  # câu hỏi slot nằm cuối


def test_knowledge_and_appointment_same_turn(client, fake_llm, rag_chunk):
    """Hỏi kỹ thuật + muốn mang xe qua: knowledge trả lời trước, appointment hiểu là sửa chữa và hỏi ngày giờ."""
    rag_chunk("Phanh kêu thường do má phanh mòn hoặc dính bụi; nên kiểm tra sớm.")
    text = "Tại sao phanh kêu, mình mang xe qua được không"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "Tại sao phanh kêu"),
                         _appt("mang xe qua"), slots=[slot("service_type", "REPAIR", "mang xe qua")])
    fake_llm.says("knowledge", "Phanh kêu thường do má phanh mòn, bạn nên kiểm tra sớm.")
    r = chat(client, text, token="t12")
    assert {"knowledge", "appointment"} <= set(r["trace"])
    reply = r["reply"].rstrip()
    assert "phanh" in reply.lower() and SLOT_ASK in reply and reply.lower().index("phanh") < reply.index(SLOT_ASK)
    assert "lái thử" not in reply  # không hỏi lại loại dịch vụ khi khách đã nói rõ triệu chứng hỏng


# ===== Kiến thức, an toàn, hợp đồng =====
def test_safety_question_has_warning(client, fake_llm, rag_chunk):
    rag_chunk("Đèn áp suất dầu sáng: dừng xe ngay và không tiếp tục chạy.")
    text = "Đèn áp suất dầu sáng thì sao"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", text), signals=PolicySignals(safety_risk=True))
    fake_llm.uses_tools("policy", [[("flag_safety_risk", {})]])
    r = chat(client, text, token="t4")
    assert "hotline" in r["reply"]


def test_knowledge_maintenance(client, fake_llm, rag_chunk):
    """Hỏi kiến thức bảo dưỡng → knowledge node trả lời."""
    rag_chunk("Nên thay dầu động cơ sau mỗi 5.000 km.")
    text = "Bao lâu thì cần thay dầu động cơ"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", text))
    fake_llm.says("knowledge", "Bạn nên thay dầu động cơ sau mỗi 5.000 km ạ.")
    r = chat(client, text, token="t6")
    assert "knowledge" in r["trace"] and "5.000 km" in r["reply"]


def test_knowledge_complexity_high_goes_to_human(client, fake_llm):
    """Câu kỹ thuật phức tạp vượt tài liệu: policy (LLM) quyết chuyển người, knowledge không chạy."""
    text = "Tại sao " + "xe kêu lạ " * 40
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "xe kêu lạ"), complexity="HIGH")
    fake_llm.uses_tools("policy", [[("handover_to_human", {"reason": "COMPLEXITY_HIGH"})]])
    r = chat(client, text, token="t10")
    assert "knowledge" not in r["trace"] and "human_handover" in r["trace"]


def test_contract_asks_for_info(client, fake_llm):
    """Yêu cầu hợp đồng → contract node hỏi thông tin."""
    text = "Mình muốn chốt mua xe"
    fake_llm.understands(text, task("CONTRACT", "start_contract", text))
    r = chat(client, text, token="t7")
    assert "contract" in r["trace"]


def test_contract_price_question_goes_to_human(client, fake_llm):
    text = "Hợp đồng này phí trước bạ bao nhiêu"
    fake_llm.understands(text, task("CONTRACT", "ask_contract_fee", text), signals=PolicySignals(deal_change=True))
    r = chat(client, text, token="t9")
    assert "contract" in r["trace"] and r["trace"][-4:] == ["compose", "output_guard", "human_handover", "respond"]


# ===== Nhân viên tiếp quản =====
def test_human_takeover_flow(client, fake_llm):
    """claim → khách được báo + bot im lặng → nhân viên nhắn → resolve → báo trả về bot."""
    with SessionLocal() as db:
        db.query(Manager).filter_by(username="Lan").delete()
        db.add(Manager(username="Lan", password_hash="x"))
        db.commit()
    r = chat(client, _hand_over(fake_llm), token="t15")
    conv = r["session_id"]
    task_id = _tasks(conv)[-1].id
    assert client.post(f"/admin/tasks/{task_id}/reply", json={"content": "hi"}).status_code == 409
    assert client.post(f"/admin/tasks/{task_id}/claim", params={"manager": "Lan"}).status_code == 200
    assert client.post(f"/admin/tasks/{task_id}/claim").status_code == 409

    p = _poll(client, conv, "t15", after=r["message_id"])
    assert p["mode"] == "HUMAN_ACTIVE" and "Lan" in p["messages"][-1]["content"]

    r2 = chat(client, "Alo", token="t15", conv=conv)  # HUMAN_ACTIVE: không gọi graph/LLM nên không cần kịch bản
    assert r2["mode"] == "HUMAN_ACTIVE" and r2["trace"] == [] and r2["reply"] == ""

    client.post(f"/admin/tasks/{task_id}/reply", json={"content": "Chào anh, em là Lan"})
    p = _poll(client, conv, "t15", after=r2["message_id"])
    assert [m["role"] for m in p["messages"]] == ["staff"]

    client.post(f"/admin/tasks/{task_id}/resolve")
    p = _poll(client, conv, "t15", after=p["messages"][-1]["id"])
    assert p["mode"] == "BOT" and p["messages"][-1]["role"] == "system"
    fake_llm.understands("Xin chào", task("GREETING", "greet", "Xin chào"))
    r3 = chat(client, "Xin chào", token="t15", conv=conv)
    assert r3["mode"] == "BOT" and r3["trace"]


def test_poll_rejects_unknown_session(client):
    # Không cần LLM: chỉ kiểm API poll với phiên không tồn tại / id sai định dạng.
    assert client.get(f"/chat/{uuid.uuid4()}/messages").status_code == 404
    assert client.get("/chat/bad id!/messages").status_code == 422
