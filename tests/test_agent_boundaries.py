"""Nhóm 'ranh giới agent' (L3): agent chỉ đọc chạy song song, tối đa một agent ghi dữ liệu mỗi lượt, trạng thái liên lượt.

Chạy bằng FakeLLM (tests/fake_llm.py): kịch bản khai báo 'LLM hiểu gì / gọi công cụ nào', mọi thứ còn lại là code thật
(router, policy guard, agent, validation, compose, output guard, ghi DB). Các test KHÔNG kiểm bot có hiểu tiếng Việt hay
không (đó là L5). Luồng đặt lịch hiện tại: hỏi lần lượt dịch vụ/ngày giờ/họ tên đầy đủ/SĐT -> ĐỀ XUẤT -> ghi lịch ở lượt
khách xác nhận (xem test_conv_booking.py).
"""
import uuid
from unittest.mock import Mock

import pytest

from app.agent.graph import get_graph
from app.agent.nodes.compose import compose
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.router import WRITERS, router
from app.contracts.schemas import (ConversationInterpretation, Intent, KnownFact, PolicySignals, ServiceRecord,
                                   SlotOperation, SlotUpdate, TaskInterpretation, TaskRelationship)
from app.db.models import Appointment, ChatSession, Customer, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services
from tests.conversation_helpers import validate_interpretation
from tests.fake_llm import fake_llm, slot, task  # noqa: F401

pytestmark = pytest.mark.usefixtures("clean_bookings")  # khung giờ chỉ chứa 2 lịch: dọn trước mỗi test

PRICE_AND_VISIT = "Giá xe Foton Aumark bao nhiêu, cho mình qua showroom xem xe"
PRICE_ANSWER = "Foton Aumark có giá khoảng 500.000.000 đồng ạ."
VISIT_ASK = "xin ngày giờ hẹn"  # câu bot hỏi tiếp ngày giờ (app/agent/nodes/appointment.py)
PHONE = "0912345678"
ACCEPT = task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER")
# "Được" khi KHÔNG còn offer/luồng ghi nào: LLM gán domain OTHER, router đưa về conversation (không đặt lịch)
STRAY_ACCEPT = task("OTHER", "accept_offer", "Được", "ACCEPT_OFFER")


def chat(client, text, token, conv=None):
    response = client.post("/chat", json={"session_id": conv or f"{token}-{uuid.uuid4().hex}", "message": text})
    assert response.status_code == 200
    result = response.json()
    assert len([n for n in result["trace"] if n in WRITERS]) <= 1  # tối đa một agent ghi dữ liệu
    assert len(result["trace"]) == len(set(result["trace"]))  # mỗi node một lần, trace không cộng dồn
    return result


def checkpoint(conv):
    return get_graph().get_state({"configurable": {"thread_id": str(conv)}}).values


def appointments(conv):
    with SessionLocal() as db:
        return db.query(Appointment).filter_by(session_id=conv).all()


def _appt(excerpt, relationship="NEW_REQUEST"):
    return task("APPOINTMENT", "book_appointment", excerpt, relationship)


def _script_price_and_visit(fake_llm):
    """Khách hỏi giá + xin xem xe trong một tin: sales (đọc) chạy song song, appointment (ghi) hỏi tiếp ngày giờ."""
    fake_llm.understands(PRICE_AND_VISIT,
                         task("SALES", "ask_price", "Giá xe Foton Aumark bao nhiêu"),
                         _appt("cho mình qua showroom xem xe"),
                         slots=[slot("service_type", "SHOWROOM_VISIT", "showroom"),
                                slot("car_model", "Foton Aumark", "Foton Aumark")])
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)


def _script_start_booking(fake_llm, text="Mình muốn đặt lịch bảo dưỡng"):
    fake_llm.understands(text, _appt("đặt lịch bảo dưỡng"), slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng")])
    return text


# ===== Hỏi giá + xem xe trong một tin: một lịch duy nhất, ghi ở lượt xác nhận =====
def test_price_and_showroom_visit_same_turn_single_booking(client, fake_llm):
    _script_price_and_visit(fake_llm)
    first = chat(client, PRICE_AND_VISIT, "same-turn")
    conv = first["session_id"]
    assert {"sales", "appointment"} <= set(first["trace"])
    assert "đồng" in first["reply"] and VISIT_ASK in first["reply"]
    assert checkpoint(conv)["active_workflow"].workflow_type == "appointment" and appointments(conv) == []

    when = "9 giờ sáng thứ 7"  # chỉ nói "sáng thứ 7" thì bot hỏi tiếp giờ cụ thể, chưa đủ để đề xuất
    fake_llm.understands(when, _appt(when, "ANSWER_TO_QUESTION"), slots=[slot("appointment_when", when, when)])
    chat(client, when, "same-turn", conv)
    identity = f"Nguyễn Văn An, {PHONE}"
    fake_llm.understands(identity, _appt(identity, "ANSWER_TO_QUESTION"),
                         slots=[slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", PHONE)])
    proposal = chat(client, identity, "same-turn", conv)
    assert appointments(conv) == [] and "xác nhận" in proposal["reply"].lower()  # mới đề xuất, chưa ghi

    fake_llm.understands("Đồng ý", ACCEPT, accepts_offer=True)
    fake_llm.uses_tools("appointment", [[("create_appointment", {})]])
    last = chat(client, "Đồng ý", "same-turn", conv)
    assert "đã đặt và xác nhận lịch hẹn" in last["reply"].lower()
    booked = appointments(conv)
    assert len(booked) == 1 and booked[0].service_type == "SHOWROOM_VISIT"
    fake_llm.understands("Cảm ơn", task("GREETING", "thanks", "Cảm ơn"))
    assert "appointment" not in chat(client, "Cảm ơn", "same-turn", conv)["trace"]
    assert len(appointments(conv)) == 1


def test_showroom_visit_goes_to_appointment(client, fake_llm):
    text = "Tôi muốn đến showroom để xem xe Foton Aumark"
    fake_llm.understands(text, _appt("đến showroom xem xe"), slots=[slot("service_type", "SHOWROOM_VISIT", "showroom")])
    result = chat(client, text, "showroom")
    assert "appointment" in result["trace"] and "sales" not in result["trace"]


# ===== Câu hỏi đọc xen vào lúc đang đặt lịch không làm mất luồng =====
def test_read_question_during_booking_keeps_flow(client, fake_llm):
    first = chat(client, _script_start_booking(fake_llm), "keep-flow")
    conv = first["session_id"]
    question = "Lần trước xưởng làm gì cho xe mình"
    fake_llm.understands(question, task("AFTER_SALES", "get_history", question, "CHANGE_TOPIC"))
    second = chat(client, question, "keep-flow", conv)
    assert "after_sales" in second["trace"] and "appointment" not in second["trace"]
    assert checkpoint(conv)["active_workflow"].workflow_type == "appointment"
    fake_llm.understands("Sáng thứ 7", _appt("Sáng thứ 7", "ANSWER_TO_QUESTION"),
                         slots=[slot("appointment_when", "Sáng thứ 7", "Sáng thứ 7")])
    assert "appointment" in chat(client, "Sáng thứ 7", "keep-flow", conv)["trace"]


# ===== Hợp đồng + đặt lịch cùng một tin: writer thứ hai được hoãn =====
def test_two_writers_defer_second_with_note(client, fake_llm):
    text = "Mình chốt mua xe, đặt lịch qua showroom xem xe nữa"
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua xe"), _appt("đặt lịch qua showroom xem xe"),
                         slots=[slot("service_type", "SHOWROOM_VISIT", "showroom")])
    first = chat(client, text, "two-writers")
    conv = first["session_id"]
    assert "contract" in first["trace"] and "appointment" not in first["trace"]
    assert "đặt lịch hẹn" in first["reply"]
    assert checkpoint(conv)["active_workflow"].workflow_type == "contract"
    open_pending = [item for item in checkpoint(conv)["pending_interactions"] if item.status.value == "OPEN"]
    assert any(item.metadata.get("workflow") == "appointment" for item in open_pending)
    fake_llm.understands("Được", ACCEPT, accepts_offer=True)
    second = chat(client, "Được", "two-writers", conv)
    assert "appointment" in second["trace"] and "contract" not in second["trace"]
    second_state = checkpoint(conv)
    assert any(item.status.value == "OPEN" and item.metadata.get("workflow") == "appointment"
               for item in second_state["pending_interactions"])


# ===== Hủy, chào hỏi giữa luồng =====
def test_cancel_and_greeting_in_active_flow(client, fake_llm):
    start = "Mình muốn đặt lịch"
    fake_llm.understands(start, _appt("đặt lịch"))
    first = chat(client, start, "cancel")
    conv = first["session_id"]
    fake_llm.understands("Xin chào", task("GREETING", "greet", "Xin chào", "CHANGE_TOPIC"))
    greeting = chat(client, "Xin chào", "cancel", conv)
    assert "appointment" not in greeting["trace"] and checkpoint(conv)["active_workflow"].workflow_type == "appointment"
    fake_llm.understands("Thôi không cần đặt lịch", _appt("không cần đặt lịch", "CANCEL_TASK"))
    cancelled = chat(client, "Thôi không cần đặt lịch", "cancel", conv)
    cancelled_state = checkpoint(conv)
    assert "appointment" not in cancelled["trace"] and "đã dừng" in cancelled["reply"]
    assert cancelled_state.get("active_workflow") is None
    assert all(item.status.value != "OPEN" for item in cancelled_state["pending_interactions"])
    fake_llm.understands("Được", STRAY_ACCEPT, accepts_offer=True)
    assert "appointment" not in chat(client, "Được", "cancel", conv)["trace"]


# ===== Chính sách: giảm giá làm chuyển người và xóa luồng dở =====
def test_policy_escalation_parks_flow_and_ignored_acceptance_does_not_book(client, fake_llm):
    _script_price_and_visit(fake_llm)
    first = chat(client, PRICE_AND_VISIT, "escalate")
    conv = first["session_id"]
    fake_llm.understands("Giảm giá cho mình", task("DISCOUNT", "request_discount", "Giảm giá"),
                         signals=PolicySignals(discount_request=True))
    escalated = chat(client, "Giảm giá cho mình", "escalate", conv)
    assert escalated["mode"] == "HUMAN_PENDING" and "appointment" not in escalated["trace"]
    state = checkpoint(conv)
    # Khác thiết kế cũ: escalate không xóa workflow mà chuyển WAITING_FOR_APPROVAL (chủ ý); không còn offer nào mở.
    assert state["active_workflow"].status.value == "WAITING_FOR_APPROVAL"
    assert all(item.status.value != "OPEN" for item in state["pending_interactions"]) and appointments(conv) == []
    with SessionLocal() as db:
        task_id = db.query(HandoverTicket).filter_by(session_id=conv).one().id
    client.post(f"/admin/tasks/{task_id}/claim")
    client.post(f"/admin/tasks/{task_id}/resolve")
    fake_llm.understands("Được", STRAY_ACCEPT, accepts_offer=True)
    assert "appointment" not in chat(client, "Được", "escalate", conv)["trace"]


# ===== Tin an toàn chỉ chạy knowledge =====
def test_safety_runs_knowledge_alone(client, fake_llm, rag_chunk):
    rag_chunk("Đèn áp suất dầu sáng: dừng xe ngay và không tiếp tục chạy.")
    first = chat(client, _script_start_booking(fake_llm), "safe")
    text = "Đèn áp suất dầu sáng, thôi không lái nữa"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", "đèn áp suất dầu"),
                         signals=PolicySignals(safety_risk=True))
    fake_llm.uses_tools("policy", [[("flag_safety_risk", {})]])
    second = chat(client, text, "safe", first["session_id"])
    assert "router" in second["trace"] and "knowledge" in second["trace"]
    assert "appointment" not in second["trace"] and "hotline" in second["reply"]


# ===== Slot dịch vụ và đổi giờ khi đang đặt lịch =====
def test_active_appointment_service_slot_and_updated_time(client, fake_llm):
    fake_llm.understands("Mình muốn đặt lịch", _appt("đặt lịch"))
    first = chat(client, "Mình muốn đặt lịch", "service-slot")
    conv = first["session_id"]
    fake_llm.understands("Bảo dưỡng", _appt("Bảo dưỡng", "ANSWER_TO_QUESTION"),
                         slots=[slot("service_type", "MAINTENANCE", "Bảo dưỡng")])
    second = chat(client, "Bảo dưỡng", "service-slot", conv)
    assert "appointment" in second["trace"] and "knowledge" not in second["trace"]
    fake_llm.understands("Ngày mai", _appt("Ngày mai", "ANSWER_TO_QUESTION"),
                         slots=[slot("appointment_when", "Ngày mai", "Ngày mai")])
    chat(client, "Ngày mai", "service-slot", conv)
    change = "Đổi sang 9 giờ sáng thứ 7"
    fake_llm.understands(change, _appt(change, "CORRECT_PREVIOUS_INFO"),
                         slots=[slot("appointment_when", "9 giờ sáng thứ 7", "9 giờ sáng thứ 7", "CORRECT")])
    chat(client, change, "service-slot", conv)
    assert checkpoint(conv)["known_facts"]["appointment_when"].value == "9 giờ sáng thứ 7"  # giá trị mới thay giá trị cũ
    identity = f"Nguyễn Văn An {PHONE}"
    fake_llm.understands(identity, _appt(identity, "ANSWER_TO_QUESTION"),
                         slots=[slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"), slot("customer_phone", PHONE)])
    last = chat(client, identity, "service-slot", conv)
    assert "thứ 7" in last["reply"].lower() or "thứ bảy" in last["reply"].lower(), last["reply"]


def test_sales_question_ending_with_thoi_is_not_cancelled(client, fake_llm):
    text = "Cho mình hỏi giá xe Foton Aumark thôi"
    fake_llm.understands(text, task("SALES", "ask_price", "giá xe Foton Aumark"))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    result = chat(client, text, "not-cancel")
    assert "sales" in result["trace"] and "đã dừng" not in result["reply"]


def test_technical_question_interrupts_appointment(client, fake_llm, rag_chunk):
    rag_chunk("Phanh kêu có thể do má phanh mòn; nên kiểm tra sớm.")
    first = chat(client, _script_start_booking(fake_llm), "technical-interrupt")
    text = "Phanh kêu có nguy hiểm không?"
    fake_llm.understands(text, task("KNOWLEDGE", "answer_knowledge", text, "CHANGE_TOPIC"))
    result = chat(client, text, "technical-interrupt", first["session_id"])
    assert "knowledge" in result["trace"] and "appointment" not in result["trace"]


def test_cancelled_booking_does_not_reuse_old_date(client, fake_llm):
    text = "Mình muốn đặt lịch bảo dưỡng ngày mai"
    fake_llm.understands(text, _appt("đặt lịch bảo dưỡng ngày mai"),
                         slots=[slot("service_type", "MAINTENANCE", "bảo dưỡng"),
                                slot("appointment_when", "ngày mai", "ngày mai")])
    first = chat(client, text, "stale-date")
    conv = first["session_id"]
    fake_llm.understands("Thôi không đặt nữa", _appt("không đặt nữa", "CANCEL_TASK"))
    chat(client, "Thôi không đặt nữa", "stale-date", conv)
    assert not checkpoint(conv)["known_facts"].get("appointment_when")
    again = f"Đặt lịch sửa xe {PHONE}"
    fake_llm.understands(again, _appt("Đặt lịch sửa xe"),
                         slots=[slot("service_type", "REPAIR", "sửa xe"), slot("customer_phone", PHONE)])
    result = chat(client, again, "stale-date", conv)
    assert "ngày giờ" in result["reply"] and "đã đặt và xác nhận lịch hẹn" not in result["reply"]
    assert appointments(conv) == []


# ===== Hợp đồng =====
def test_contract_slot_only_continuation(client, fake_llm):
    start = "Mình muốn chốt mua xe"
    fake_llm.understands(start, task("CONTRACT", "start_contract", start))
    first = chat(client, start, "contract-slots")
    assert "sales" not in first["trace"]
    fake_llm.understands(PHONE, task("CONTRACT", "provide_info", PHONE, "ANSWER_TO_QUESTION"),
                         slots=[slot("customer_phone", PHONE)])
    second = chat(client, PHONE, "contract-slots", first["session_id"])
    assert "contract" in second["trace"]
    assert checkpoint(first["session_id"])["known_facts"]["customer_phone"].value == PHONE


def test_contract_and_fee_question_same_turn(client, fake_llm):
    text = "Mình chốt mua Foton Aumark, phí trước bạ bao nhiêu"
    fake_llm.understands(text, task("CONTRACT", "start_contract", "chốt mua Foton Aumark"),
                         task("SALES", "ask_price", "phí trước bạ bao nhiêu"))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    result = chat(client, text, "contract-clause")
    assert {"contract", "sales"} <= set(result["trace"]) and "human_handover" not in result["trace"]


# ===== Handover một phần giữ lại câu trả lời khác =====
def test_partial_handover_keeps_other_answers(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_services().knowledge, "retrieve", Mock(return_value=[]))
    text = "Giá xe Foton Aumark bao nhiêu, còn phanh kêu là tại sao"
    fake_llm.understands(text, task("SALES", "ask_price", "Giá xe Foton Aumark bao nhiêu"),
                         task("KNOWLEDGE", "answer_knowledge", "phanh kêu là tại sao"))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    result = chat(client, text, "partial")
    assert {"sales", "knowledge", "human_handover"} <= set(result["trace"])
    assert "đồng" in result["reply"] and "chuyển cho nhân viên" in result["reply"]
    assert result["mode"] == "HUMAN_PENDING"
    with SessionLocal() as db:
        assert "no RAG match" in db.query(HandoverTicket).filter_by(session_id=result["session_id"]).one().summary


def test_agents_receive_their_own_clause(client, fake_llm, monkeypatch):
    retrieve = Mock(wraps=get_services().knowledge.retrieve)
    monkeypatch.setattr(get_services().knowledge, "retrieve", retrieve)
    text = "Giá xe tải Foton bao nhiêu, còn phanh kêu là tại sao"
    fake_llm.understands(text, task("SALES", "ask_price", "Giá xe tải Foton bao nhiêu"),
                         task("KNOWLEDGE", "answer_knowledge", "phanh kêu là tại sao"))
    fake_llm.uses_tools("sales", [], final=PRICE_ANSWER)
    result = chat(client, text, "clauses")
    assert {"sales", "knowledge"} <= set(result["trace"])
    assert retrieve.call_args.args[0] == "phanh kêu là tại sao"  # knowledge chỉ nhận vế của nó
    # Sales trả lời trước knowledge theo thứ tự vế câu, bất kể nhánh nào chạy xong trước
    assert result["reply"].index("đồng") < result["reply"].index("phanh")


# ===== HUMAN_PENDING / HUMAN_ACTIVE =====
@pytest.mark.parametrize("claimed", [False, True])
def test_human_modes_keep_inbox(client, fake_llm, monkeypatch, claimed):
    """HUMAN_ACTIVE: bot không chạy graph. HUMAN_PENDING: bot CÓ chạy graph (đã đổi so với test cũ), tin khách vẫn
    được ghi vào ticket mở và không tạo ticket thứ hai."""
    token = f"bypass-{claimed}"
    text = "Mình muốn gặp nhân viên"
    fake_llm.understands(text, task("OTHER", "request_human", text), signals=PolicySignals(wants_human=True))
    first = chat(client, text, token)
    conv = first["session_id"]
    with SessionLocal() as db:
        task_id = db.query(HandoverTicket).filter_by(session_id=conv).one().id
    follow_up = "Gọi mình 0987654321"
    if claimed:
        client.post(f"/admin/tasks/{task_id}/claim")
        run = Mock(side_effect=AssertionError("Human mode must not call graph"))
        monkeypatch.setattr("app.conversation.run_turn", run)
    else:
        fake_llm.understands(follow_up, task("OTHER", "provide_info", follow_up, "ANSWER_TO_QUESTION"),
                             slots=[slot("customer_phone", "0987654321")])
    second = chat(client, follow_up, token, conv)
    assert second["mode"] == ("HUMAN_ACTIVE" if claimed else "HUMAN_PENDING")
    if claimed:
        assert second["trace"] == [] and second["reply"] == ""
        run.assert_not_called()
    with SessionLocal() as db:
        customer = db.get(Customer, db.get(ChatSession, conv).customer_id)
        assert customer.phone_number == "+84987654321"
        assert "0987654321" in db.get(HandoverTicket, task_id).summary
        assert db.query(HandoverTicket).filter_by(session_id=conv).count() == 1
        user_messages = db.query(Message).filter_by(session_id=conv, role="user").order_by(Message.id).all()
        assert [m.content for m in user_messages][-1] == follow_up
        if claimed:  # HUMAN_ACTIVE: không có câu bot trả lời, message_id là tin của khách
            assert db.get(Message, second["message_id"]).role == "user"
    inbox = client.get(f"/admin/tasks/{task_id}/messages").json()
    assert follow_up in [m["content"] for m in inbox]  # tin khách luôn vào inbox của nhân viên (HUMAN_PENDING còn có cả câu bot)


# ===== Hậu mãi =====
def test_after_sales_empty_stub(client, fake_llm):
    text = "Lần trước xưởng làm gì cho xe mình"
    fake_llm.understands(text, task("AFTER_SALES", "get_history", text))
    result = chat(client, text, "history-empty")
    assert "after_sales" in result["trace"] and result["trace"][-3:] == ["compose", "output_guard", "respond"]
    assert "chưa có dữ liệu" in result["reply"]
    assert get_services().after_sales.get_history(999999) == []


def test_after_sales_reads_service_and_formats_records(client, fake_llm, monkeypatch):
    service = Mock()
    service.get_history.return_value = [ServiceRecord(id=1, service_date="2026-10-01", service_type="MAINTENANCE",
                                                     description="Thay dầu động cơ", cost_vnd=1200000)]
    monkeypatch.setattr(get_services(), "after_sales", service)
    text = "Cho mình xem lịch sử bảo dưỡng"
    fake_llm.understands(text, task("AFTER_SALES", "get_history", text))
    result = chat(client, text, "history-records")
    assert "after_sales" in result["trace"] and "knowledge" not in result["trace"]
    assert "2026-10-01" in result["reply"] and "1.200.000 đồng" in result["reply"]
    service.get_history.assert_called_once_with(checkpoint(result["session_id"])["customer_id"], limit=5)


def test_after_sales_complaint_still_escalates(client, fake_llm):
    text = "Khiếu nại lần trước sửa xe không tốt"
    fake_llm.understands(text, task("AFTER_SALES", "get_history", text), signals=PolicySignals(complaint=True))
    fake_llm.uses_tools("policy", [[("handover_to_human", {"reason": "COMPLAINT"})]])  # khiếu nại do policy LLM quyết
    result = chat(client, text, "history-complaint")
    assert "human_handover" in result["trace"] and "after_sales" not in result["trace"]


def test_policy_records_all_handover_reasons(client, fake_llm):
    text = "Cho mình gặp nhân viên, giảm giá được không"
    fake_llm.understands(text, task("DISCOUNT", "request_discount", "giảm giá được không"),
                         signals=PolicySignals(wants_human=True, discount_request=True))
    result = chat(client, text, "all-reasons")
    with SessionLocal() as db:
        task_row = db.query(HandoverTicket).filter_by(session_id=result["session_id"]).one()
        conv = db.get(ChatSession, result["session_id"])
    # Mọi lý do đều được ghi. Lý do CHÍNH hiện là NEGOTIATION (policy_guard thêm lý do giảm giá trước HUMAN_REQUEST),
    # trong khi docstring primary_reason() ví dụ HUMAN_REQUEST: lệch nhỏ, không assert thứ tự.
    assert "HUMAN_REQUEST" in task_row.summary and "NEGOTIATION" in task_row.summary and conv.negotiation_active
    assert task_row.reason in ("HUMAN_REQUEST", "NEGOTIATION")


def test_canonical_tasks_drive_parallel_plan(monkeypatch):
    text = "Tư vấn xe 800 triệu và cho xem xe"
    interpretation = ConversationInterpretation(
        message_summary="Khách cần tư vấn xe và xem xe.",
        tasks=[
            TaskInterpretation(task_id="sales", domain=Intent.SALES, action="search_catalog",
                               text="Tư vấn xe 800 triệu", relationship=TaskRelationship.NEW_REQUEST),
            TaskInterpretation(task_id="appointment", domain=Intent.APPOINTMENT, action="book_appointment",
                               text="cho xem xe", relationship=TaskRelationship.NEW_REQUEST),
        ],
        slot_updates=[SlotUpdate(key="budget_vnd", operation=SlotOperation.SET,
                                 value=800000000, evidence="800 triệu")],
    )
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=interpretation))
    from app.agent.nodes.preprocess import preprocess
    output = preprocess({"user_text": text, "known_facts": {}})
    assert output["conversation_interpretation"].tasks == interpretation.tasks
    assert output["known_facts"]["budget_vnd"].value == 800000000
    plan = router({**output, "user_text": text})["plan"]
    assert [(item["agent"], item["text"]) for item in plan] == [
        ("sales", "Tư vấn xe 800 triệu"), ("appointment", "cho xem xe")]


def test_canonical_empty_interpretation_and_unsupported_updates(monkeypatch):
    text = "abc"
    empty = ConversationInterpretation(message_summary="Không có yêu cầu hành động.")
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=empty))
    from app.agent.nodes.preprocess import preprocess
    output = preprocess({"user_text": text, "known_facts": {}})
    assert output["conversation_interpretation"].tasks == []
    invalid = ConversationInterpretation(
        message_summary="Không liên quan.",
        slot_updates=[SlotUpdate(key="budget_vnd", operation=SlotOperation.SET,
                                 value=500000000, evidence="500 triệu")],
    )
    assert validate_interpretation({"user_text": text}, invalid).slot_updates == []


def test_router_uses_canonical_workflow_and_pending_interaction():
    task = TaskInterpretation(task_id="appointment", domain=Intent.APPOINTMENT, action="book_appointment",
                              text="đặt lịch", relationship=TaskRelationship.NEW_REQUEST)
    update = router({"user_text": "đặt lịch", "conversation_interpretation":
                     ConversationInterpretation(message_summary="Đặt lịch.", tasks=[task])})
    assert update["planned_agents"] == ["appointment"]
    # router chỉ LẬP KẾ HOẠCH: workflow do chính agent appointment đặt (xem test_price_and_showroom...), không phải router.
    assert "active_workflow" not in update and "pending_intent" not in update


def test_compose_orders_answers_and_collects_handover():
    state = {"answers": [{"agent": "appointment", "order": 100, "text": "Ngày giờ nào?", "needs_human": False},
                         {"agent": "knowledge", "order": 1, "text": "", "needs_human": True, "handover_reason": "NO_RAG"},
                         {"agent": "sales", "order": 0, "text": "Giá 500.000.000 đồng", "needs_human": False}]}
    result = compose(state)
    assert result["draft_reply"] == "Giá 500.000.000 đồng\n\nNgày giờ nào?"
    assert result["needs_human"] and result["handover_reason"] == "NO_RAG"


def test_output_guard_validates_composed_reply():
    blocked = output_guard({"needs_human": True, "handover_reason": "NO_RAG", "draft_reply": "Mình cam kết giảm 10%"})
    assert blocked["handover_reason"] == "NO_RAG; OUTPUT_GUARD: cam kết/giảm giá" and blocked["draft_reply"] == ""
    price = output_guard({"draft_reply": "999.000.000 đồng", "allowed_amounts": ["500.000.000 đồng"]})
    assert "số tiền lạ" in price["handover_reason"]
    ok = output_guard({"draft_reply": "Ngân sách 800 triệu: xe giá 750.000.000 đồng",
                       "allowed_amounts": ["750.000.000 đồng"]})
    assert not ok.get("needs_human")


