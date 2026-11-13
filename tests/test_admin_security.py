"""Admin: khóa đăng nhập sai liên tiếp và xác nhận lịch hẹn (gán khung giờ, báo khách)."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.auth import hash_password
from app.config import settings
from app.contracts.schemas import ServiceType
from app.db.models import Appointment, AppointmentSlot, Manager, Message
from app.db.session import SessionLocal
from app.services.booking import DbBookingService

DAY = date(2026, 12, 1)


@pytest.fixture()
def auth_on(monkeypatch):
    cfg = replace(settings, admin_auth_required=True, admin_secret="test-secret",
                  admin_max_login_attempts=3, admin_lock_minutes=15, slot_capacity=1)
    for mod in ("app.auth", "app.api.admin", "app.services.booking"):
        monkeypatch.setattr(f"{mod}.settings", cfg)
    return cfg


def _manager(db, username="an", password="dung-roi", role="sales_admin"):
    db.add(Manager(username=username, password_hash=hash_password(password), role=role))
    db.commit()


def _login(client, password, username="an"):
    return client.post("/admin/login", json={"username": username, "password": password})


def test_admin_requires_token(client, db_session, auth_on):
    assert client.get("/admin/conversations").status_code == 401
    assert client.get("/admin/appointments", headers={"Authorization": "Bearer x.y"}).status_code == 401


def test_third_wrong_password_locks_account(client, db_session, auth_on):
    _manager(db_session)
    assert _login(client, "sai").status_code == 401
    assert _login(client, "sai").status_code == 401
    third = _login(client, "sai")
    assert third.status_code == 429 and "khóa" in third.json()["detail"]
    assert _login(client, "dung-roi").status_code == 429  # đang khóa: đúng mật khẩu cũng không vào được


def test_lock_expires_and_success_resets_counter(client, db_session, auth_on):
    _manager(db_session)
    for _ in range(3):
        _login(client, "sai")
    with SessionLocal() as db:  # giả lập hết hạn khóa
        db.query(Manager).update({"locked_until": datetime.now(timezone.utc) - timedelta(seconds=1)})
        db.commit()
    assert _login(client, "dung-roi").status_code == 200
    assert _login(client, "sai").status_code == 401  # bộ đếm đã về 0 sau khi đăng nhập đúng
    assert _login(client, "sai").status_code == 401
    assert _login(client, "dung-roi").status_code == 200
    assert _login(client, "sai", username="khong-co").status_code == 401  # tài khoản lạ: không bị 429, không lộ


def _token(client, role="sales_admin"):
    return {"Authorization": "Bearer " + _login(client, "dung-roi").json()["token"]}


def _book_auto(session_id, start=time(9, 0), phone="0912345601"):
    """Đặt qua service như bot: còn chỗ thì lịch CONFIRMED ngay, đã giữ slot."""
    r = DbBookingService().create_appointment(session_id, ServiceType.MAINTENANCE, DAY, start, phone, "Xe: FUSO")
    assert r.ok and r.status == "CONFIRMED"
    return r.appointment_id


def _book(session_id, start=time(9, 0), phone="0912345601"):
    """Như _book_auto rồi đưa lịch về PENDING_APPROVAL (trả slot) để test luồng nhân viên duyệt/đổi/hủy thủ công."""
    appt = _book_auto(session_id, start, phone)
    with SessionLocal() as db:
        row = db.get(Appointment, appt)
        slot = db.get(AppointmentSlot, row.slot_id)
        slot.booked_count -= 1
        row.slot_id, row.status = None, "PENDING_APPROVAL"
        db.commit()
    return appt


def test_confirm_assigns_slot_and_notifies_customer(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-confirm-0001")
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=h).json() == {"ok": True, "status": "CONFIRMED"}
    with SessionLocal() as db:
        row = db.get(Appointment, appt)
        slot = db.get(AppointmentSlot, row.slot_id)
        assert (row.status, slot.booked_count, slot.capacity) == ("CONFIRMED", 1, 1)
        note = db.query(Message).filter_by(session_id="sess-confirm-0001", role="system").one()
        assert "đã được nhân viên xác nhận" in note.content and "09:00" in note.content
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=h).status_code == 409  # không xác nhận 2 lần
    assert client.post("/admin/appointments/999999/confirm", headers=h).status_code == 404


def test_confirm_full_slot_and_reschedule_releases(client, db_session, auth_on, monkeypatch):
    _manager(db_session)
    h = _token(client)
    # Đặt khi sức chứa còn 2 (đặt lịch chặn vượt sức chứa), rồi siết còn 1 để kiểm tra bước xác nhận của nhân viên.
    monkeypatch.setattr("app.services.booking.settings", replace(auth_on, slot_capacity=2))
    first, second = _book("sess-full-000001"), _book("sess-full-000002", phone="0912345602")
    monkeypatch.setattr("app.services.booking.settings", auth_on)
    assert client.post(f"/admin/appointments/{first}/confirm", headers=h).status_code == 200
    full = client.post(f"/admin/appointments/{second}/confirm", headers=h)
    assert full.status_code == 409 and "đủ chỗ" in full.json()["detail"]
    # khách đổi giờ qua bot: trả chỗ khung cũ, tự xác nhận ở khung mới, lịch kia xác nhận được
    DbBookingService().reschedule_appointment("sess-full-000001", first, DAY, time(10, 0))
    with SessionLocal() as db:
        assert db.get(Appointment, first).status == "CONFIRMED"
        assert db.query(AppointmentSlot).filter_by(starts_at=datetime(2026, 12, 1, 9, 0, tzinfo=timezone(timedelta(hours=7)))).one().booked_count == 0
    assert client.post(f"/admin/appointments/{second}/confirm", headers=h).status_code == 200


def test_confirm_needs_time_when_customer_gave_none(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-notime-00001", start=None)
    need = client.post(f"/admin/appointments/{appt}/confirm", headers=h)
    assert need.status_code == 422 and "giờ" in need.json()["detail"]
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=h, json={"start_time": "99:99"}).status_code == 422
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=h, json={"start_time": "14:30"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(Appointment, appt).start_time == time(14, 30)
