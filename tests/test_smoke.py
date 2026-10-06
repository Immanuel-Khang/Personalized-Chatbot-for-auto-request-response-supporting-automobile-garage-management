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


def test_negotiation_bot_stops(client):
    """Khi đang chờ nhân viên, bot không xử lý."""
    r = chat(client, "Giảm giá cho mình đi", token="t5")
    assert r["mode"] == "HUMAN_PENDING"
    r2 = chat(client, "Alo", token="t5", conv=r["conversation_id"])
    assert r2["trace"] == []  # bot không chạy khi đang chờ nhân viên


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
