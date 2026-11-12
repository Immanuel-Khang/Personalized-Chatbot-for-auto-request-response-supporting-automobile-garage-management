"""Nhân viên hủy / đổi lịch hẹn từ trang admin: trả chỗ, kiểm tra đầy/trùng, báo khách trong chat."""
from datetime import date, time, timedelta

from app.db.models import Appointment, AppointmentSlot, Message
from app.db.session import SessionLocal
from tests.test_admin_security import _book, _manager, _token, auth_on  # noqa: F401 - auth_on là fixture

LATER = date.today() + timedelta(days=40)


def _notes(session_id):
    with SessionLocal() as db:
        return [m.content for m in db.query(Message).filter_by(session_id=session_id, role="system").order_by(Message.id)]


def _row(appt):
    with SessionLocal() as db:
        row = db.get(Appointment, appt)
        slot = db.get(AppointmentSlot, row.slot_id) if row.slot_id else None
        return row.status, row.appointment_date, row.start_time, (slot.booked_count if slot else None)


def _post(client, h, path, **body):
    return client.post(path, headers=h, json=body)


# ---- hủy ----
def test_cancel_frees_slot_and_notifies_customer(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-stcan-000001")
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=h).status_code == 200
    r = _post(client, h, f"/admin/appointments/{appt}/cancel", reason="Xưởng nghỉ đột xuất")
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "CANCELLED"}
    status, _, _, booked = _row(appt)
    assert status == "CANCELLED" and booked is None  # slot_id đã gỡ
    with SessionLocal() as db:
        assert db.query(AppointmentSlot).one().booked_count == 0  # chỗ trong khung giờ đã trả
    note = _notes("sess-stcan-000001")[-1]
    assert f"#{appt}" in note and "đã được nhân viên hủy" in note and "Xưởng nghỉ đột xuất" in note


def test_cancel_without_body_and_pending_appointment(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-stcan-000002")
    assert client.post(f"/admin/appointments/{appt}/cancel", headers=h).status_code == 200  # không cần body
    assert "Lý do" not in _notes("sess-stcan-000002")[-1]


def test_cancel_rules(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    assert client.post("/admin/appointments/999999/cancel", headers=h).status_code == 404
    appt = _book("sess-stcan-000003")
    assert client.post(f"/admin/appointments/{appt}/cancel", headers=h).status_code == 200
    again = client.post(f"/admin/appointments/{appt}/cancel", headers=h)
    assert again.status_code == 409  # đã hủy rồi
    assert len(_notes("sess-stcan-000003")) == 1  # lần hủy lỗi không báo khách thêm
    done = _book("sess-stcan-000004", phone="0912345604")
    client.post(f"/admin/appointments/{done}/confirm", headers=h)
    client.post(f"/admin/appointments/{done}/complete", headers=h)
    assert client.post(f"/admin/appointments/{done}/cancel", headers=h).status_code == 409  # DONE không hủy được
    assert client.post(f"/admin/appointments/{appt}/cancel").status_code == 401  # cần đăng nhập


# ---- đổi lịch ----
def test_reschedule_pending_stays_pending_and_notifies(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-strsc-000001")
    r = _post(client, h, f"/admin/appointments/{appt}/reschedule", appointment_date=LATER.isoformat(),
              start_time="10:30", reason="Thợ chính bận")
    assert r.status_code == 200 and r.json()["status"] == "PENDING_APPROVAL"
    assert _row(appt)[:3] == ("PENDING_APPROVAL", LATER, time(10, 30))
    note = _notes("sess-strsc-000001")[-1]
    assert f"đổi sang 10:30 ngày {LATER:%d/%m/%Y}" in note and "Thợ chính bận" in note and "chờ nhân viên xác nhận lại" in note


def test_reschedule_confirmed_is_reconfirmed_and_moves_slot(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-strsc-000002")
    client.post(f"/admin/appointments/{appt}/confirm", headers=h)
    r = _post(client, h, f"/admin/appointments/{appt}/reschedule", appointment_date=LATER.isoformat(), start_time="14:00")
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
    status, day, start, booked = _row(appt)
    assert (status, day, start, booked) == ("CONFIRMED", LATER, time(14, 0), 1)
    with SessionLocal() as db:
        counts = sorted((s.starts_at.date().isoformat(), s.booked_count) for s in db.query(AppointmentSlot))
    assert counts[0][1] == 0 and counts[-1] == (LATER.isoformat(), 1)  # slot cũ đã trả, slot mới đã giữ
    assert "Lịch đã được xác nhận" in _notes("sess-strsc-000002")[-1]


def test_reschedule_to_full_window_is_refused_and_keeps_old_time(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)  # auth_on: sức chứa 1 lịch/khung
    first = _book("sess-strsc-000003")
    other = _book("sess-strsc-000004", start=time(11, 0), phone="0912345604")
    _post(client, h, f"/admin/appointments/{first}/reschedule", appointment_date=LATER.isoformat(), start_time="09:00")
    full = _post(client, h, f"/admin/appointments/{other}/reschedule", appointment_date=LATER.isoformat(), start_time="09:20")
    assert full.status_code == 409 and "đủ chỗ" in full.json()["detail"]
    assert _row(other)[1] == date(2026, 12, 1)  # giữ nguyên ngày cũ
    assert len(_notes("sess-strsc-000004")) == 0  # bị từ chối thì không báo khách


def test_reschedule_validation(client, db_session, auth_on):
    _manager(db_session)
    h = _token(client)
    appt = _book("sess-strsc-000005")
    url = f"/admin/appointments/{appt}/reschedule"
    assert _post(client, h, url, appointment_date="khong-phai-ngay").status_code == 422
    assert _post(client, h, url, appointment_date="2020-01-01").status_code == 422  # ngày đã qua
    assert _post(client, h, url, appointment_date=LATER.isoformat(), start_time="25:99").status_code == 422
    assert _post(client, h, "/admin/appointments/999999/reschedule", appointment_date=LATER.isoformat()).status_code == 404
    assert _notes("sess-strsc-000005") == []
    # không có giờ -> chấp nhận (chưa chiếm chỗ), vẫn chờ xác nhận
    ok = _post(client, h, url, appointment_date=LATER.isoformat())
    assert ok.status_code == 200 and _row(appt)[2] is None
