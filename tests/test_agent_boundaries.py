"""Regression tests: agent chỉ đọc chạy song song, tối đa một agent ghi dữ liệu mỗi lượt, trạng thái liên lượt."""
import uuid
from unittest.mock import Mock

import pytest

from app.agent.graph import get_graph
from app.agent.nodes.compose import compose
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.preprocess import _apply_interpretation, _trace_safe, validate_interpretation
from app.agent.nodes.router import WRITERS, router
from app.agent.nodes.segments import split_clauses


def classify_clause(text: str) -> set[str]:
    """Minimal stub for segment classification tests."""
    words = text.lower()
    intents = set()
    # Only classify if actually contains SALES-specific keywords, not just "và" splits
    if any(w in words for w in ("giá", "bao nhiêu", "so sánh")):
        intents.add("SALES")
    # Don't classify bare brand names to allow merge behavior
    if "foton" in words and len(words.split()) > 1:
        intents.add("SALES")
    if "isuzu" in words and len(words.split()) > 1:
        intents.add("SALES")
    if any(w in words for w in ("phanh", "kêu", "tại sao", "nguy hiểm")):
        intents.add("KNOWLEDGE")
    return intents
from app.contracts.schemas import (ConversationInterpretation, Intent, KnownFact, ServiceRecord, SlotOperation,
                                   SlotUpdate, TaskInterpretation, TaskRelationship)
from app.db.models import Appointment, ChatSession, Customer, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services


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


def test_price_and_test_drive_same_turn_single_booking(client):
    first = chat(client, "Giá xe Foton Aumark bao nhiêu, cho mình chạy thử", "same-turn")
    conv = first["session_id"]
    assert {"sales", "appointment"} <= set(first["trace"])
    assert "đồng" in first["reply"] and first["reply"].rstrip().endswith("Bạn muốn đến vào ngày giờ nào?")
    assert checkpoint(conv)["active_workflow"].workflow_type == "appointment" and appointments(conv) == []

    assert "appointment" not in chat(client, "Sáng thứ 7", "isolated")["trace"]  # hội thoại khác không bị ảnh hưởng
    chat(client, "Sáng thứ 7", "same-turn", conv)
    last = chat(client, "0912345678", "same-turn", conv)
    assert "ghi nhận lịch hẹn" in last["reply"]
    assert checkpoint(conv)["active_workflow"].status.value == "WAITING_FOR_APPROVAL"
    assert "appointment" not in chat(client, "Cảm ơn", "same-turn", conv)["trace"]
    booked = appointments(conv)
    assert len(booked) == 1 and booked[0].service_type == "TEST_DRIVE"


def test_showroom_visit_goes_to_appointment(client):
    result = chat(client, "Tôi muốn đến showroom để xem xe Foton Aumark", "showroom")
    assert "appointment" in result["trace"] and "sales" not in result["trace"]


def test_read_question_during_booking_keeps_flow(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng", "keep-flow")
    conv = first["session_id"]
    second = chat(client, "Lần trước xưởng làm gì cho xe mình", "keep-flow", conv)
    assert "after_sales" in second["trace"] and "appointment" not in second["trace"]
    assert checkpoint(conv)["active_workflow"].workflow_type == "appointment"
    assert "appointment" in chat(client, "Sáng thứ 7", "keep-flow", conv)["trace"]


def test_two_writers_defer_second_with_note(client):
    first = chat(client, "Mình chốt mua xe, đặt lịch lái thử nữa", "two-writers")
    conv = first["session_id"]
    assert "contract" in first["trace"] and "appointment" not in first["trace"]
    assert "đặt lịch hẹn" in first["reply"]
    assert checkpoint(conv)["active_workflow"].workflow_type == "appointment"
    open_pending = [item for item in checkpoint(conv)["pending_interactions"] if item.status.value == "OPEN"]
    assert open_pending and open_pending[-1].metadata["missing_slots"] == ["customer_phone"]
    second = chat(client, "Được", "two-writers", conv)
    assert "appointment" in second["trace"] and "contract" not in second["trace"]
    second_state = checkpoint(conv)
    assert any(item.status.value == "OPEN" and item.metadata.get("workflow") == "appointment"
               for item in second_state["pending_interactions"])


def test_cancel_and_greeting_in_active_flow(client):
    first = chat(client, "Mình muốn đặt lịch", "cancel")
    conv = first["session_id"]
    greeting = chat(client, "Xin chào", "cancel", conv)
    greeting_state = checkpoint(conv)
    assert "appointment" not in greeting["trace"] and greeting_state["active_workflow"].workflow_type == "appointment"
    cancelled = chat(client, "Thôi không cần đặt lịch", "cancel", conv)
    cancelled_state = checkpoint(conv)
    assert "appointment" not in cancelled["trace"] and "đã dừng" in cancelled["reply"]
    assert cancelled_state.get("active_workflow") is None
    assert all(item.status.value != "OPEN" for item in cancelled_state["pending_interactions"])
    assert "appointment" not in chat(client, "Được", "cancel", conv)["trace"]


def test_policy_escalation_clears_flow(client):
    first = chat(client, "Giá xe Foton Aumark bao nhiêu, cho mình chạy thử", "escalate")
    conv = first["session_id"]
    escalated = chat(client, "Giảm giá cho mình", "escalate", conv)
    assert escalated["mode"] == "HUMAN_PENDING" and "appointment" not in escalated["trace"]
    state = checkpoint(conv)
    assert state.get("active_workflow") is None and all(
        item.status.value != "OPEN" for item in state["pending_interactions"]
    )
    with SessionLocal() as db:
        task_id = db.query(HandoverTicket).filter_by(session_id=conv).one().id
    client.post(f"/admin/tasks/{task_id}/claim")
    client.post(f"/admin/tasks/{task_id}/resolve")
    assert "appointment" not in chat(client, "Được", "escalate", conv)["trace"]


def test_safety_runs_knowledge_alone(client):
    first = chat(client, "Mình muốn đặt lịch", "safe")
    second = chat(client, "Đèn áp suất dầu sáng, thôi không lái nữa", "safe", first["session_id"])
    assert "router" in second["trace"] and "knowledge" in second["trace"]
    assert "appointment" not in second["trace"] and "hotline" in second["reply"]


def test_active_appointment_service_slot_and_updated_time(client):
    first = chat(client, "Mình muốn đặt lịch", "service-slot")
    conv = first["session_id"]
    second = chat(client, "Bảo dưỡng", "service-slot", conv)
    assert "appointment" in second["trace"] and "knowledge" not in second["trace"]
    chat(client, "Ngày mai", "service-slot", conv)
    chat(client, "Đổi sang thứ 7", "service-slot", conv)
    last = chat(client, "0912345678", "service-slot", conv)
    assert "thứ 7" in last["reply"]


def test_sales_question_ending_with_thoi_is_not_cancelled(client):
    result = chat(client, "Cho mình hỏi giá xe Foton Aumark thôi", "not-cancel")
    assert "sales" in result["trace"] and "đã dừng" not in result["reply"]


def test_technical_question_interrupts_appointment(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng", "technical-interrupt")
    result = chat(client, "Phanh kêu có nguy hiểm không?", "technical-interrupt", first["session_id"])
    assert "knowledge" in result["trace"] and "appointment" not in result["trace"]


def test_cancelled_booking_does_not_reuse_old_date(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng ngày mai", "stale-date")
    conv = first["session_id"]
    chat(client, "Thôi không đặt nữa", "stale-date", conv)
    assert not checkpoint(conv)["known_facts"].get("appointment_when")
    result = chat(client, "Đặt lịch lái thử 0912345678", "stale-date", conv)
    assert "ngày giờ" in result["reply"] and "ghi nhận lịch hẹn" not in result["reply"]
    assert appointments(conv) == []


def test_contract_slot_only_continuation(client):
    first = chat(client, "Mình muốn chốt mua xe", "contract-slots")
    assert "sales" not in first["trace"]
    second = chat(client, "0912345678", "contract-slots", first["session_id"])
    assert "contract" in second["trace"]
    assert checkpoint(first["session_id"])["known_facts"]["customer_phone"].value == "0912345678"


def test_contract_and_fee_question_same_turn(client):
    result = chat(client, "Mình chốt mua Foton Aumark, phí trước bạ bao nhiêu", "contract-clause")
    assert {"contract", "sales"} <= set(result["trace"]) and "human_handover" not in result["trace"]


def test_partial_handover_keeps_other_answers(client, monkeypatch):
    monkeypatch.setattr(get_services().knowledge, "retrieve", Mock(return_value=[]))
    result = chat(client, "Giá xe Foton Aumark bao nhiêu, còn phanh kêu là tại sao", "partial")
    assert {"sales", "knowledge", "human_handover"} <= set(result["trace"])
    assert "đồng" in result["reply"] and "chuyển yêu cầu" in result["reply"]
    assert result["mode"] == "HUMAN_PENDING"
    with SessionLocal() as db:
        assert "no RAG match" in db.query(HandoverTicket).filter_by(session_id=result["session_id"]).one().summary


def test_agents_receive_their_own_clause(client, monkeypatch):
    retrieve = Mock(wraps=get_services().knowledge.retrieve)
    monkeypatch.setattr(get_services().knowledge, "retrieve", retrieve)
    search = Mock(wraps=get_services().product.search)
    monkeypatch.setattr(get_services().product, "search", search)
    result = chat(client, "Giá xe tải Foton bao nhiêu, còn phanh kêu là tại sao", "clauses")
    assert {"sales", "knowledge"} <= set(result["trace"])
    assert search.call_args.args[0] == "Giá xe tải Foton bao nhiêu"
    assert retrieve.call_args.args[0] == "phanh kêu là tại sao"
    # Sales trả lời trước knowledge theo thứ tự vế câu, bất kể nhánh nào chạy xong trước
    assert result["reply"].index("đồng") < result["reply"].index("phanh")


@pytest.mark.parametrize("claimed", [False, True])
def test_human_modes_bypass_graph_and_keep_inbox(client, monkeypatch, claimed):
    token = f"bypass-{claimed}"
    first = chat(client, "Mình muốn gặp nhân viên", token)
    conv = first["session_id"]
    with SessionLocal() as db:
        task_id = db.query(HandoverTicket).filter_by(session_id=conv).one().id
    if claimed:
        client.post(f"/admin/tasks/{task_id}/claim")
    run = Mock(side_effect=AssertionError("Human mode must not call graph"))
    monkeypatch.setattr("app.conversation.run_turn", run)
    second = chat(client, "Gọi mình 0987654321", token, conv)
    assert second["trace"] == [] and second["reply"] == ""
    assert second["mode"] == ("HUMAN_ACTIVE" if claimed else "HUMAN_PENDING")
    run.assert_not_called()
    with SessionLocal() as db:
        customer = db.get(Customer, db.get(ChatSession, conv).customer_id)
        assert customer.phone_number == "+84987654321"
        assert "0987654321" in db.get(HandoverTicket, task_id).summary
        assert db.query(HandoverTicket).filter_by(session_id=conv).count() == 1
        assert db.query(Message).filter_by(session_id=conv, role="ai").count() == 1
        assert db.get(Message, second["message_id"]).role == "user"
    assert client.get(f"/admin/tasks/{task_id}/messages").json()[-1]["content"] == "Gọi mình 0987654321"


def test_after_sales_empty_stub(client):
    result = chat(client, "Lần trước xưởng làm gì cho xe mình", "history-empty")
    assert "after_sales" in result["trace"] and result["trace"][-3:] == ["compose", "output_guard", "respond"]
    assert "chưa có dữ liệu" in result["reply"]
    assert get_services().after_sales.get_history(999999) == []


def test_after_sales_reads_service_and_formats_records(client, monkeypatch):
    service = Mock()
    service.get_history.return_value = [ServiceRecord(id=1, service_date="2026-10-01", service_type="MAINTENANCE",
                                                     description="Thay dầu động cơ", cost_vnd=1200000)]
    monkeypatch.setattr(get_services(), "after_sales", service)
    result = chat(client, "Cho mình xem lịch sử bảo dưỡng", "history-records")
    assert "after_sales" in result["trace"] and "knowledge" not in result["trace"]
    assert "2026-10-01" in result["reply"] and "1.200.000 đồng" in result["reply"]
    service.get_history.assert_called_once_with(checkpoint(result["session_id"])["customer_id"], limit=5)


def test_after_sales_complaint_still_escalates(client):
    result = chat(client, "Khiếu nại lần trước sửa xe không tốt", "history-complaint")
    assert "human_handover" in result["trace"] and "after_sales" not in result["trace"]


def test_policy_records_all_handover_reasons(client):
    result = chat(client, "Cho mình gặp nhân viên, giảm giá được không", "all-reasons")
    with SessionLocal() as db:
        task = db.query(HandoverTicket).filter_by(session_id=result["session_id"]).one()
        conv = db.get(ChatSession, result["session_id"])
    assert task.reason == "HUMAN_REQUEST" and "NEGOTIATION" in task.summary and conv.negotiation_active


def test_canonical_tasks_drive_parallel_plan(monkeypatch):
    text = "Tư vấn xe 800 triệu và cho lái thử"
    interpretation = ConversationInterpretation(
        message_summary="Khách cần tư vấn xe và lái thử.",
        tasks=[
            TaskInterpretation(task_id="sales", domain=Intent.SALES, action="search_catalog",
                               text="Tư vấn xe 800 triệu", relationship=TaskRelationship.NEW_REQUEST),
            TaskInterpretation(task_id="appointment", domain=Intent.APPOINTMENT, action="book_appointment",
                               text="cho lái thử", relationship=TaskRelationship.NEW_REQUEST),
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
        ("sales", "Tư vấn xe 800 triệu"), ("appointment", "cho lái thử")]


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
    assert update["active_workflow"]["workflow_type"] == "appointment"
    assert "pending_intent" not in update


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


def test_llm_budget_search_regression(client, monkeypatch):
    interpretation = ConversationInterpretation(
        message_summary="Khách tìm xe trong ngân sách 800 triệu.",
        tasks=[TaskInterpretation(task_id="sales", domain=Intent.SALES, action="search_catalog",
                                  text="Tư vấn xe trong ngân sách 800 triệu",
                                  relationship=TaskRelationship.NEW_REQUEST)],
        slot_updates=[SlotUpdate(key="budget_vnd", operation=SlotOperation.SET,
                                 value=800000000, evidence="800 triệu")],
    )
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=interpretation))
    product = get_services().product
    search = Mock(wraps=product.search_by_budget)
    monkeypatch.setattr(product, "search_by_budget", search)
    response = chat(client, "Tư vấn xe trong ngân sách 800 triệu", "budget")
    search.assert_called_once_with(800000000, limit=3)
    assert "sales" in response["trace"] and response["mode"] == "BOT" and "đồng" in response["reply"]


def test_split_clauses_keeps_original_text():
    assert split_clauses("So sánh Foton và Isuzu", classify_clause) == [("So sánh Foton và Isuzu", {"SALES"})]
    assert [c for c, _ in split_clauses("Xe giá 1.5 tỷ phải không. Phanh kêu tại sao", classify_clause)] == \
        ["Xe giá 1.5 tỷ phải không", "Phanh kêu tại sao"]
