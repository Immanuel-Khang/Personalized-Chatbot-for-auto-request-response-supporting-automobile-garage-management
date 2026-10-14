"""Regression tests: agent chỉ đọc chạy song song, tối đa một agent ghi dữ liệu mỗi lượt, trạng thái liên lượt."""
from unittest.mock import Mock

import pytest

from app.agent.graph import get_graph
from app.agent.nodes.compose import compose
from app.agent.nodes.output_guard import output_guard
from app.agent.nodes.preprocess import _llm_preprocess, _rule_based_preprocess, classify_clause
from app.agent.nodes.router import WRITERS, router
from app.agent.nodes.segments import split_clauses
from app.contracts.schemas import CustomerSlots, Intent, IntentAndSlotExtraction, IntentTask, ServiceRecord
from app.db.models import Appointment, Conversation, Customer, HumanTask, Message
from app.db.session import SessionLocal
from app.services import get_services


def chat(client, text, token, conv=None):
    response = client.post("/chat", json={"visitor_token": token, "message": text, "conversation_id": conv})
    assert response.status_code == 200
    result = response.json()
    assert len([n for n in result["trace"] if n in WRITERS]) <= 1  # tối đa một agent ghi dữ liệu
    assert len(result["trace"]) == len(set(result["trace"]))  # mỗi node một lần, trace không cộng dồn
    return result


def checkpoint(conv):
    return get_graph().get_state({"configurable": {"thread_id": str(conv)}}).values


def appointments(conv):
    with SessionLocal() as db:
        return db.query(Appointment).filter_by(customer_id=checkpoint(conv)["customer_id"]).all()


def test_price_and_test_drive_same_turn_single_booking(client):
    first = chat(client, "Giá xe Vios bao nhiêu, cho mình chạy thử", "same-turn")
    conv = first["conversation_id"]
    assert {"sales", "appointment"} <= set(first["trace"])
    assert "đồng" in first["reply"] and first["reply"].rstrip().endswith("Bạn muốn đến vào ngày giờ nào?")
    assert checkpoint(conv)["active_flow"] == "appointment" and appointments(conv) == []

    assert "appointment" not in chat(client, "Sáng thứ 7", "isolated")["trace"]  # hội thoại khác không bị ảnh hưởng
    chat(client, "Sáng thứ 7", "same-turn", conv)
    last = chat(client, "0912345678", "same-turn", conv)
    assert "ghi nhận lịch hẹn" in last["reply"] and checkpoint(conv)["active_flow"] is None
    assert "appointment" not in chat(client, "Cảm ơn", "same-turn", conv)["trace"]
    booked = appointments(conv)
    assert len(booked) == 1 and booked[0].service_type == "TEST_DRIVE"


def test_camry_showroom_visit_goes_to_appointment(client):
    result = chat(client, "Tôi muốn đến showroom để xem xe toyota camry 2.5Q", "showroom")
    assert "appointment" in result["trace"] and "sales" not in result["trace"]


def test_read_question_during_booking_keeps_flow(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng", "keep-flow")
    conv = first["conversation_id"]
    second = chat(client, "Lần trước xưởng làm gì cho xe mình", "keep-flow", conv)
    assert "after_sales" in second["trace"] and "appointment" not in second["trace"]
    assert checkpoint(conv)["active_flow"] == "appointment"
    assert "appointment" in chat(client, "Sáng thứ 7", "keep-flow", conv)["trace"]


def test_two_writers_defer_second_with_note(client):
    first = chat(client, "Mình chốt mua xe, đặt lịch lái thử nữa", "two-writers")
    conv = first["conversation_id"]
    assert "contract" in first["trace"] and "appointment" not in first["trace"]
    assert "đặt lịch hẹn" in first["reply"]
    assert checkpoint(conv)["pending_intent"] == "APPOINTMENT"
    second = chat(client, "Được", "two-writers", conv)
    assert "appointment" in second["trace"] and "contract" not in second["trace"]
    assert checkpoint(conv)["pending_intent"] is None


def test_cancel_and_greeting_in_active_flow(client):
    first = chat(client, "Mình muốn đặt lịch", "cancel")
    conv = first["conversation_id"]
    greeting = chat(client, "Xin chào", "cancel", conv)
    assert "appointment" not in greeting["trace"] and checkpoint(conv)["active_flow"] == "appointment"
    cancelled = chat(client, "Thôi không cần đặt lịch", "cancel", conv)
    assert "appointment" not in cancelled["trace"] and "đã dừng" in cancelled["reply"]
    assert checkpoint(conv)["pending_intent"] is None and checkpoint(conv).get("active_flow") is None
    assert "appointment" not in chat(client, "Được", "cancel", conv)["trace"]


def test_policy_escalation_clears_flow(client):
    first = chat(client, "Giá xe Vios bao nhiêu, cho mình chạy thử", "escalate")
    conv = first["conversation_id"]
    escalated = chat(client, "Giảm giá cho mình", "escalate", conv)
    assert escalated["mode"] == "HUMAN_PENDING" and "appointment" not in escalated["trace"]
    assert checkpoint(conv)["active_flow"] is None and checkpoint(conv)["pending_intent"] is None
    with SessionLocal() as db:
        task_id = db.query(HumanTask).filter_by(conversation_id=conv).one().id
    client.post(f"/admin/tasks/{task_id}/claim")
    client.post(f"/admin/tasks/{task_id}/resolve")
    assert "appointment" not in chat(client, "Được", "escalate", conv)["trace"]


def test_safety_runs_knowledge_alone(client):
    first = chat(client, "Mình muốn đặt lịch", "safe")
    second = chat(client, "Đèn áp suất dầu sáng, thôi không lái nữa", "safe", first["conversation_id"])
    assert "router" in second["trace"] and "knowledge" in second["trace"]
    assert "appointment" not in second["trace"] and "hotline" in second["reply"]


def test_active_appointment_service_slot_and_updated_time(client):
    first = chat(client, "Mình muốn đặt lịch", "service-slot")
    conv = first["conversation_id"]
    second = chat(client, "Bảo dưỡng", "service-slot", conv)
    assert "appointment" in second["trace"] and "knowledge" not in second["trace"]
    chat(client, "Ngày mai", "service-slot", conv)
    chat(client, "Đổi sang thứ 7", "service-slot", conv)
    last = chat(client, "0912345678", "service-slot", conv)
    assert "thứ 7" in last["reply"]


def test_sales_question_ending_with_thoi_is_not_cancelled(client):
    result = chat(client, "Cho mình hỏi giá xe Vios thôi", "not-cancel")
    assert "sales" in result["trace"] and "đã dừng" not in result["reply"]


def test_technical_question_interrupts_appointment(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng", "technical-interrupt")
    result = chat(client, "Phanh kêu có nguy hiểm không?", "technical-interrupt", first["conversation_id"])
    assert "knowledge" in result["trace"] and "appointment" not in result["trace"]


def test_cancelled_booking_does_not_reuse_old_date(client):
    first = chat(client, "Mình muốn đặt lịch bảo dưỡng ngày mai", "stale-date")
    conv = first["conversation_id"]
    chat(client, "Thôi không đặt nữa", "stale-date", conv)
    assert not checkpoint(conv)["slots"].get("appointment_when")
    result = chat(client, "Đặt lịch lái thử 0912345678", "stale-date", conv)
    assert "ngày giờ" in result["reply"] and "ghi nhận lịch hẹn" not in result["reply"]
    assert appointments(conv) == []


def test_contract_slot_only_continuation(client):
    first = chat(client, "Mình muốn chốt mua xe", "contract-slots")
    assert "sales" not in first["trace"]
    second = chat(client, "0912345678", "contract-slots", first["conversation_id"])
    assert "contract" in second["trace"]
    assert checkpoint(first["conversation_id"])["slots"]["customer_phone"] == "0912345678"


def test_contract_and_fee_question_same_turn(client):
    result = chat(client, "Mình chốt mua Vios, phí trước bạ bao nhiêu", "contract-clause")
    assert {"contract", "sales"} <= set(result["trace"]) and "human_handover" not in result["trace"]


def test_partial_handover_keeps_other_answers(client, monkeypatch):
    monkeypatch.setattr(get_services().knowledge, "retrieve", Mock(return_value=[]))
    result = chat(client, "Giá xe Vios bao nhiêu, còn phanh kêu là tại sao", "partial")
    assert {"sales", "knowledge", "human_handover"} <= set(result["trace"])
    assert "đồng" in result["reply"] and "chuyển yêu cầu" in result["reply"]
    assert result["mode"] == "HUMAN_PENDING"
    with SessionLocal() as db:
        assert "no RAG match" in db.query(HumanTask).filter_by(conversation_id=result["conversation_id"]).one().reason


def test_agents_receive_their_own_clause(client, monkeypatch):
    retrieve = Mock(wraps=get_services().knowledge.retrieve)
    monkeypatch.setattr(get_services().knowledge, "retrieve", retrieve)
    search = Mock(wraps=get_services().product.search)
    monkeypatch.setattr(get_services().product, "search", search)
    result = chat(client, "Giá xe 7 chỗ bao nhiêu, còn phanh kêu là tại sao", "clauses")
    assert {"sales", "knowledge"} <= set(result["trace"])
    assert search.call_args.args[0] == "Giá xe 7 chỗ bao nhiêu"
    assert retrieve.call_args.args[0] == "phanh kêu là tại sao"
    # Sales trả lời trước knowledge theo thứ tự vế câu, bất kể nhánh nào chạy xong trước
    assert result["reply"].index("đồng") < result["reply"].index("phanh")


@pytest.mark.parametrize("claimed", [False, True])
def test_human_modes_bypass_graph_and_keep_inbox(client, monkeypatch, claimed):
    token = f"bypass-{claimed}"
    first = chat(client, "Mình muốn gặp nhân viên", token)
    conv = first["conversation_id"]
    with SessionLocal() as db:
        task_id = db.query(HumanTask).filter_by(conversation_id=conv).one().id
    if claimed:
        client.post(f"/admin/tasks/{task_id}/claim")
    run = Mock(side_effect=AssertionError("Human mode must not call graph"))
    monkeypatch.setattr("app.conversation.run_turn", run)
    second = chat(client, "Gọi mình 0987654321", token, conv)
    assert second["trace"] == [] and second["reply"] == ""
    assert second["mode"] == ("HUMAN_ACTIVE" if claimed else "HUMAN_PENDING")
    run.assert_not_called()
    with SessionLocal() as db:
        assert db.query(Customer).filter_by(visitor_token=token).one().phone == "0987654321"
        assert "0987654321" in db.get(HumanTask, task_id).summary
        assert db.query(HumanTask).filter_by(conversation_id=conv).count() == 1
        assert db.query(Message).filter_by(conversation_id=conv, role="bot").count() == 1
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
    service.get_history.assert_called_once_with(checkpoint(result["conversation_id"])["customer_id"], limit=5)


def test_after_sales_complaint_still_escalates(client):
    result = chat(client, "Khiếu nại lần trước sửa xe không tốt", "history-complaint")
    assert "human_handover" in result["trace"] and "after_sales" not in result["trace"]


def test_policy_records_all_handover_reasons(client):
    result = chat(client, "Cho mình gặp nhân viên, giảm giá được không", "all-reasons")
    with SessionLocal() as db:
        task = db.query(HumanTask).filter_by(conversation_id=result["conversation_id"]).one()
        conv = db.get(Conversation, result["conversation_id"])
    assert "HUMAN_REQUEST" in task.reason and "NEGOTIATION" in task.reason and conv.negotiation_active


def test_llm_tasks_drive_parallel_plan(monkeypatch):
    text = "Tư vấn xe 800 triệu và cho lái thử"
    result = IntentAndSlotExtraction(
        intents=[Intent.SALES, Intent.APPOINTMENT, Intent.SALES],
        tasks=[IntentTask(intent=Intent.SALES, text="Tư vấn xe 800 triệu"),
               IntentTask(intent=Intent.APPOINTMENT, text="cho lái thử")],
        extracted_slots=CustomerSlots(budget_vnd=800000000, service_type="TEST_DRIVE"))
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=result))
    output = _llm_preprocess({"user_text": text, "slots": {"customer_name": "Khách"}})
    assert output["intents"] == ["SALES", "APPOINTMENT"]
    assert output["slots"] == {"customer_name": "Khách", "budget_vnd": 800000000, "service_type": "TEST_DRIVE"}
    plan = router({**output, "user_text": text})["plan"]
    assert [(p["agent"], p["text"]) for p in plan] == [("sales", "Tư vấn xe 800 triệu"), ("appointment", "cho lái thử")]


def test_llm_without_tasks_and_rule_fallback(monkeypatch):
    mock = Mock(return_value=IntentAndSlotExtraction(intents=[Intent.KNOWLEDGE]))
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", mock)
    assert _llm_preprocess({"user_text": "abc"})["tasks"] == [{"intent": "KNOWLEDGE", "text": "abc"}]
    mock.return_value = IntentAndSlotExtraction(intents=[])
    assert _llm_preprocess({"user_text": "abc"})["intents"] == ["OTHER"]
    mock.return_value = None
    fallback = _llm_preprocess({"user_text": "Giá xe bao nhiêu, cho mình chạy thử"})
    assert set(fallback["intents"]) == {"SALES", "APPOINTMENT"} and fallback["complexity"] == "LOW"


def test_router_slot_answer_and_pending_consumption():
    slot = _rule_based_preprocess({"user_text": "0912345678", "active_flow": "appointment"})
    assert slot["intents"] == ["OTHER"] and slot["slots_updated"]
    assert router({**slot, "user_text": "0912345678", "active_flow": "appointment"})["planned_agents"] == ["appointment"]
    update = router({"intents": ["OTHER"], "user_text": "Được", "pending_intent": "APPOINTMENT", "pending_text": "lái thử"})
    assert update["plan"] == [{"agent": "appointment", "text": "lái thử", "order": 100}]
    assert update["pending_intent"] is None
    merged = router({"tasks": [{"intent": "MAINTENANCE", "text": "mốc 10.000 km"},
                               {"intent": "KNOWLEDGE", "text": "tại sao phanh kêu"}], "user_text": "x"})
    assert merged["plan"] == [{"agent": "knowledge", "text": "mốc 10.000 km, tại sao phanh kêu", "order": 0}]


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
    result = IntentAndSlotExtraction(intents=[Intent.SALES], extracted_slots=CustomerSlots(budget_vnd=800000000))
    monkeypatch.setattr("app.agent.nodes.preprocess.generate_structured", Mock(return_value=result))
    product = get_services().product
    search = Mock(wraps=product.search_by_budget)
    monkeypatch.setattr(product, "search_by_budget", search)
    response = chat(client, "Tư vấn xe trong ngân sách 800 triệu", "budget")
    search.assert_called_once_with(800000000, limit=3)
    assert "sales" in response["trace"] and response["mode"] == "BOT" and "đồng" in response["reply"]


def test_split_clauses_keeps_original_text():
    assert split_clauses("So sánh Vios và Camry", classify_clause) == [("So sánh Vios và Camry", {"SALES"})]
    assert [c for c, _ in split_clauses("Xe giá 1.5 tỷ phải không. Phanh kêu tại sao", classify_clause)] == \
        ["Xe giá 1.5 tỷ phải không", "Phanh kêu tại sao"]
