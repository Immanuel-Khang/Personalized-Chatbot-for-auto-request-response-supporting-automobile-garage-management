"""Smoke test: bảo đảm khung sườn chạy được đầu-cuối. Mỗi tính năng mới nên thêm 1 test ở đây hoặc file riêng."""
import uuid


def chat(client, text, token="t1", conv=None):
    r = client.post("/chat", json={"session_id": conv or f"{token}-{uuid.uuid4().hex}", "message": text})
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


def test_pending_bot_silent(client):
    """Đang chờ nhân viên, không gọi graph và không tạo ticket mới."""
    r = chat(client, "Giảm giá cho mình đi", token="t5")
    assert r["mode"] == "HUMAN_PENDING"
    r2 = chat(client, "Cho mình hỏi giá xe Toyota Vios", token="t5", conv=r["session_id"])
    assert r2["mode"] == "HUMAN_PENDING" and r2["trace"] == [] and r2["reply"] == ""


def test_pending_extra_human_request_appends_ticket(client):
    """Đang chờ mà khách lại mặc cả → ghi thêm vào ticket đang mở, không mở ticket thứ 2."""
    r = chat(client, "Giảm giá cho mình đi", token="t16")
    r2 = chat(client, "Bớt thêm chút nữa được không, mình khiếu nại đấy", token="t16", conv=r["session_id"])
    assert r2["trace"] == [] and r2["reply"] == ""
    assert "khiếu nại" in _tasks(r["session_id"])[-1].summary
    assert len(_tasks(r["session_id"])) == 1


def test_appointment_slot_filling_multi_turn(client):
    r = chat(client, "Mình muốn đặt lịch bảo dưỡng", token="t3")
    r = chat(client, "sáng thứ 7", token="t3", conv=r["session_id"])
    r = chat(client, "0912345678", token="t3", conv=r["session_id"])
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
    assert "contract" in r["trace"] and r["trace"][-4:] == ["compose", "output_guard", "human_handover", "respond"]


def test_knowledge_complexity_high_goes_to_human(client):
    r = chat(client, "Tại sao " + "xe kêu lạ " * 40, token="t10")
    assert "knowledge" not in r["trace"] and "human_handover" in r["trace"]


def test_sales_and_appointment_same_turn(client):
    """Giá + lái thử: sales báo giá và appointment hỏi slot trong cùng một lượt, câu hỏi slot nằm cuối."""
    r = chat(client, "Giá xe Vios bao nhiêu, cho mình chạy thử", token="t11")
    assert {"sales", "appointment"} <= set(r["trace"])
    assert r["trace"][-3:] == ["compose", "output_guard", "respond"]
    assert "đồng" in r["reply"] and r["reply"].rstrip().endswith("Bạn muốn đến vào ngày giờ nào?")


def test_knowledge_and_appointment_same_turn(client):
    r = chat(client, "Tại sao phanh kêu, mình mang xe qua được không", token="t12")
    assert {"knowledge", "appointment"} <= set(r["trace"])
    assert r["reply"].rstrip().endswith("Bạn muốn đặt lịch bảo dưỡng, sửa chữa hay lái thử xe?")



def _tasks(conv_id):
    from app.db.models import HandoverTicket
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return db.query(HandoverTicket).filter_by(session_id=conv_id).order_by(HandoverTicket.id).all()


def _poll(client, conv_id, token, after=0):
    r = client.get(f"/chat/{conv_id}/messages", params={"after": after})
    assert r.status_code == 200
    return r.json()


def test_pending_saves_phone_to_ticket(client):
    r = chat(client, "Mình muốn gặp nhân viên", token="t13")
    chat(client, "0987654321", token="t13", conv=r["session_id"])
    assert "0987654321" in _tasks(r["session_id"])[-1].summary


def test_pending_timeout_sends_hotline_once(client):
    """Quá thời hạn chờ → thông báo system poll được, không chạy bot, chỉ gửi 1 lần."""
    from datetime import timedelta
    from app.db.models import HandoverTicket
    from app.db.session import SessionLocal
    r = chat(client, "Mình muốn khiếu nại", token="t14")
    with SessionLocal() as db:
        task = db.get(HandoverTicket, _tasks(r["session_id"])[-1].id)
        task.sla_due_at -= timedelta(hours=1)
        db.commit()
    r2 = chat(client, "Xin chào", token="t14", conv=r["session_id"])
    assert r2["mode"] == "HUMAN_PENDING" and r2["reply"] == "" and r2["trace"] == []
    p = _poll(client, r["session_id"], "t14", after=r2["message_id"])
    assert len(p["messages"]) == 1 and "hotline" in p["messages"][0]["content"]
    r3 = chat(client, "Xin chào", token="t14", conv=r["session_id"])
    assert r3["reply"] == ""
    assert _poll(client, r["session_id"], "t14", after=p["messages"][0]["id"])["messages"] == []


def test_human_takeover_flow(client):
    """claim → khách được báo + bot im lặng → nhân viên nhắn → resolve → báo trả về bot."""
    from app.db.models import Manager
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        db.add(Manager(username="Lan", password_hash="x"))
        db.commit()
    r = chat(client, "Mình muốn gặp nhân viên", token="t15")
    conv = r["session_id"]
    task_id = _tasks(conv)[-1].id
    assert client.post(f"/admin/tasks/{task_id}/reply", json={"content": "hi"}).status_code == 409
    assert client.post(f"/admin/tasks/{task_id}/claim", params={"manager": "Lan"}).status_code == 200
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


def test_poll_rejects_unknown_session(client):
    chat(client, "Xin chào", token="t17")
    assert client.get(f"/chat/{uuid.uuid4()}/messages").status_code == 404
    assert client.get("/chat/bad id!/messages").status_code == 422
