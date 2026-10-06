"""Smoke test: bảo đảm khung sườn chạy được đầu-cuối. Mỗi tính năng mới nên thêm 1 test ở đây hoặc file riêng."""


def chat(client, text, token="t1", conv=None):
    r = client.post("/chat", json={"visitor_token": token, "message": text, "conversation_id": conv})
    assert r.status_code == 200
    return r.json()


def test_greeting(client):
    r = chat(client, "Xin chào")
    assert "trợ lý" in r["reply"].lower() or "chào" in r["reply"].lower()


def test_sales_shows_price_with_date(client):
    r = chat(client, "Cho mình hỏi giá xe Toyota Vios")
    assert "đồng" in r["reply"] or "VNĐ" in r["reply"] or "vios" in r["reply"].lower()


def test_negotiation_goes_to_human(client):
    """Giảm giá > ngưỡng (không có car_model rõ ràng) → chuyển người."""
    r = chat(client, "Giảm giá cho mình 10% nhé", token="t2")
    assert r["mode"] == "HUMAN_PENDING" and "human_handover" in r["trace"]


def test_pending_bot_keeps_answering(client):
    """Đang chờ nhân viên, bot vẫn trả lời câu hỏi khác, không tạo ticket mới."""
    r = chat(client, "Giảm giá cho mình đi", token="t5")
    assert r["mode"] == "HUMAN_PENDING"
    r2 = chat(client, "Cho mình hỏi giá xe Toyota Vios", token="t5", conv=r["conversation_id"])
    assert r2["mode"] == "HUMAN_PENDING" and "sales" in r2["trace"] and "human_handover" not in r2["trace"]


def test_pending_extra_human_request_appends_ticket(client):
    """Đang chờ mà khách lại mặc cả → ghi thêm vào ticket đang mở, không mở ticket thứ 2."""
    r = chat(client, "Giảm giá cho mình đi", token="t16")
    r2 = chat(client, "Bớt thêm chút nữa được không, mình khiếu nại đấy", token="t16", conv=r["conversation_id"])
    assert "human_handover" in r2["trace"] and "ghi chú thêm" in r2["reply"]
    assert len(_tasks(r["conversation_id"])) == 1


def test_appointment_slot_filling_multi_turn(client):
    r = chat(client, "Mình muốn đặt lịch bảo dưỡng", token="t3")
    r = chat(client, "sáng thứ 7", token="t3", conv=r["conversation_id"])
    r = chat(client, "0912345678", token="t3", conv=r["conversation_id"])
    assert "ghi nhận lịch hẹn" in r["reply"]


def test_safety_question_has_warning(client):
    r = chat(client, "Đèn áp suất dầu sáng thì sao", token="t4")
    assert "hotline" in r["reply"]


def test_knowledge_maintenance(client):
    """Hỏi kiến thức bảo dưỡng → knowledge node trả lời."""
    r = chat(client, "Bao lâu thì cần thay dầu động cơ", token="t6")
    assert "knowledge" in r["trace"]


def test_contract_asks_for_info(client):
    """Yêu cầu hợp đồng → contract node hỏi thông tin."""
    r = chat(client, "Mình muốn chốt mua xe", token="t7")
    assert "contract" in r["trace"]


def test_discount_with_car_model_goes_to_human(client):
    """Không còn auto-approve: kể cả nêu rõ xe + % nhỏ vẫn chuyển người."""
    r = chat(client, "Xe Vios giảm giá cho mình 3% nhé", token="t8")
    assert r["mode"] == "HUMAN_PENDING" and "human_handover" in r["trace"] and "sales" not in r["trace"]


def test_contract_price_question_goes_to_human(client):
    r = chat(client, "Hợp đồng này phí trước bạ bao nhiêu", token="t9")
    assert r["trace"][-3:] == ["contract", "human_handover", "respond"]


def test_knowledge_complexity_high_goes_to_human(client):
    r = chat(client, "Tại sao " + "xe kêu lạ " * 40, token="t10")
    assert "knowledge" in r["trace"] and "human_handover" in r["trace"]


def test_sales_hands_off_to_appointment(client):
    r = chat(client, "Giá xe Vios bao nhiêu, cho mình chạy thử", token="t11")
    assert r["trace"][-4:] == ["sales", "appointment", "output_guard", "respond"]


def test_knowledge_hands_off_to_appointment(client):
    r = chat(client, "Tại sao phanh kêu, mình mang xe qua được không", token="t12")
    assert r["trace"][-4:] == ["knowledge", "appointment", "output_guard", "respond"]



def _tasks(conv_id):
    from app.db.models import HumanTask
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return db.query(HumanTask).filter_by(conversation_id=conv_id).order_by(HumanTask.id).all()


def _poll(client, conv_id, token, after=0):
    r = client.get(f"/chat/{conv_id}/messages", params={"visitor_token": token, "after": after})
    assert r.status_code == 200
    return r.json()


def test_pending_saves_phone_to_ticket(client):
    r = chat(client, "Mình muốn gặp nhân viên", token="t13")
    chat(client, "0987654321", token="t13", conv=r["conversation_id"])
    assert "0987654321" in _tasks(r["conversation_id"])[-1].summary


def test_pending_timeout_sends_hotline_once(client):
    """Quá HANDOVER_TIMEOUT_MINUTES chưa ai nhận → kèm hotline vào câu trả lời, chỉ 1 lần."""
    from datetime import timedelta
    from app.db.models import HumanTask
    from app.db.session import SessionLocal
    r = chat(client, "Mình muốn khiếu nại", token="t14")
    with SessionLocal() as db:
        task = db.get(HumanTask, _tasks(r["conversation_id"])[-1].id)
        task.created_at -= timedelta(hours=1)
        db.commit()
    r2 = chat(client, "Xin chào", token="t14", conv=r["conversation_id"])
    assert r2["mode"] == "HUMAN_PENDING" and "hotline" in r2["reply"] and r2["trace"]
    r3 = chat(client, "Xin chào", token="t14", conv=r["conversation_id"])
    assert "hotline" not in r3["reply"]


def test_human_takeover_flow(client):
    """claim → khách được báo + bot im lặng → nhân viên nhắn → resolve → báo trả về bot."""
    r = chat(client, "Mình muốn gặp nhân viên", token="t15")
    conv = r["conversation_id"]
    task_id = _tasks(conv)[-1].id
    assert client.post(f"/admin/tasks/{task_id}/reply", json={"content": "hi"}).status_code == 409
    assert client.post(f"/admin/tasks/{task_id}/claim", params={"assignee": "Lan"}).status_code == 200
    assert client.post(f"/admin/tasks/{task_id}/claim").status_code == 409

    p = _poll(client, conv, "t15", after=r["message_id"])
    assert p["mode"] == "HUMAN_ACTIVE" and "Lan" in p["messages"][-1]["content"]

    r2 = chat(client, "Alo", token="t15", conv=conv)
    assert r2["mode"] == "HUMAN_ACTIVE" and r2["trace"] == [] and r2["reply"] == ""

    client.post(f"/admin/tasks/{task_id}/reply", json={"content": "Chào anh, em là Lan"})
    p = _poll(client, conv, "t15", after=r2["message_id"])
    assert [m["role"] for m in p["messages"]] == ["staff"]

    client.post(f"/admin/tasks/{task_id}/resolve")
    p = _poll(client, conv, "t15", after=p["messages"][-1]["id"])
    assert p["mode"] == "BOT" and p["messages"][-1]["role"] == "system"
    r3 = chat(client, "Xin chào", token="t15", conv=conv)
    assert r3["mode"] == "BOT" and r3["trace"]


def test_poll_rejects_other_customer(client):
    r = chat(client, "Xin chào", token="t17")
    resp = client.get(f"/chat/{r['conversation_id']}/messages", params={"visitor_token": "intruder"})
    assert resp.status_code == 404
