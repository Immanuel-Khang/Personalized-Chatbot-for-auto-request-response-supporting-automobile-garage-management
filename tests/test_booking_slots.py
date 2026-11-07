"""Khung giờ đặt lịch: báo đầy/trùng lúc đặt, chỉnh sức chứa + độ dài khung giờ từ admin, xem tải theo ngày."""
from datetime import date, time

from app.contracts.schemas import ServiceType
from app.db.models import Appointment, AppointmentSlot, Message
from app.db.session import SessionLocal
from app.services.booking import DbBookingService
from app.services.slots import BookingConfig, validate, window_start, work_windows
from tests.test_admin_security import _book, _login, _manager, auth_on  # noqa: F401 - auth_on là fixture

DAY = date(2026, 12, 1)


def _hdr(client, username):
    return {"Authorization": "Bearer " + _login(client, "dung-roi", username).json()["token"]}


def _put(client, headers, **body):
    return client.put("/admin/settings/booking", headers=headers, json=body)


def _book_as(session_id, start, phone):
    return DbBookingService().create_appointment(session_id, ServiceType.MAINTENANCE, DAY, start, phone, "Xe: FUSO")


def _manager_headers(client, db_session):
    _manager(db_session, "boss", role="manager")
    return _hdr(client, "boss")


# ---- tính khung giờ (thuần) ----
def test_window_grid_math():
    cfg = BookingConfig(slot_capacity=2, slot_minutes=90, work_start=time(8, 0), work_end=time(17, 0))
    assert window_start(time(9, 29), cfg) == time(8, 0) and window_start(time(9, 30), cfg) == time(9, 30)
    assert [f"{w:%H:%M}" for w in work_windows(cfg)] == ["08:00", "09:30", "11:00", "12:30", "14:00", "15:30"]
    assert validate(0, 60, time(8), time(17)) and validate(2, 60, time(17), time(8)) and validate(2, 60, time(8), time(17)) is None


# ---- cấu hình từ admin ----
def test_settings_manager_only_and_validated(client, db_session, auth_on):
    _manager(db_session, "sale", role="sales_admin")
    boss = _manager_headers(client, db_session)
    sale = _hdr(client, "sale")
    assert client.get("/admin/settings/booking", headers=sale).json()["can_edit"] is False
    assert _put(client, sale, slot_capacity=3).status_code == 403
    for bad in ({"slot_capacity": 0}, {"slot_minutes": 5}, {"work_start": "18:00"}, {"work_start": "xx"}):
        assert _put(client, boss, **bad).status_code == 422
    ok = _put(client, boss, slot_capacity=3, slot_minutes=90)
    assert ok.status_code == 200 and (ok.json()["slot_capacity"], ok.json()["slot_minutes"]) == (3, 90)
    assert client.get("/admin/settings/booking", headers=sale).json()["slot_capacity"] == 3  # đã lưu, mọi vai thấy


# ---- báo đầy chỗ / trùng lịch lúc đặt ----
def test_booking_rejected_when_window_full_then_cancel_frees(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=2, slot_minutes=60)
    first = _book("sess-cap-0000001", time(9, 0), "0912345611")
    _book("sess-cap-0000002", time(9, 30), "0912345612")  # cùng khung 09:00-10:00
    third = _book_as("sess-cap-0000003", time(9, 15), "0912345613")
    assert not third.ok and third.status == "SLOT_FULL" and "2/2" in third.message
    assert third.alternatives == ["08:00", "10:00", "11:00"]
    assert _book_as("sess-cap-0000003", time(10, 0), "0912345613").ok  # khung khác vẫn đặt được
    DbBookingService().cancel_appointment("sess-cap-0000001", first)  # hủy trả chỗ
    assert _book_as("sess-cap-0000004", time(9, 15), "0912345614").ok


def test_same_customer_cannot_double_book_a_window(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=3)
    first = _book("sess-dup-00000001", time(9, 0), "0912345621")
    again = _book_as("sess-dup-00000002", time(9, 40), "0912345621")  # cùng SĐT = cùng khách
    assert not again.ok and again.status == "DUPLICATE" and again.appointment_id == first


def test_check_slot_matches_booking_and_reschedule_is_guarded(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=1)
    mine = _book("sess-chk-0000001", time(9, 0), "0912345651")
    other = _book("sess-chk-0000002", time(10, 0), "0912345652")
    svc = DbBookingService()
    assert svc.check_slot("sess-chk-0000003", ServiceType.MAINTENANCE, DAY, time(9, 30)).status == "SLOT_FULL"
    assert svc.check_slot("sess-chk-0000003", ServiceType.MAINTENANCE, DAY, time(11, 0)).ok
    assert svc.check_slot("sess-chk-0000003", ServiceType.MAINTENANCE, DAY, None).ok  # chưa có giờ -> chưa chiếm chỗ
    moved = svc.reschedule_appointment("sess-chk-0000002", other, DAY, time(9, 0))  # sang khung đã đầy
    assert not moved.ok and moved.status == "SLOT_FULL"
    assert svc.check_slot("sess-chk-0000001", ServiceType.MAINTENANCE, DAY, time(9, 0), exclude_id=mine).ok  # không tự trùng chính mình


# ---- độ dài khung giờ ----
def test_slot_length_changes_the_grid(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=1, slot_minutes=120)  # 08-10, 10-12 ...
    _book("sess-len-0000001", time(9, 0), "0912345631")
    assert _book_as("sess-len-0000002", time(9, 45), "0912345632").status == "SLOT_FULL"
    _put(client, boss, slot_minutes=30)  # 09:00-09:30 và 09:30-10:00 là hai khung khác nhau
    assert _book_as("sess-len-0000002", time(9, 45), "0912345632").ok


# ---- xem tải theo ngày + xác nhận theo khung giờ ----
def test_slot_overview_and_confirm_respects_new_capacity(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=2, slot_minutes=60)
    a = _book("sess-ovw-0000001", time(9, 0), "0912345641")
    b = _book("sess-ovw-0000002", time(9, 30), "0912345642")
    assert client.post(f"/admin/appointments/{a}/confirm", headers=boss).status_code == 200
    window = next(w for w in client.get("/admin/slots?date=2026-12-01", headers=boss).json()["windows"] if w["start"] == "09:00")
    assert (window["confirmed"], window["pending"], window["free"], window["capacity"]) == (1, 1, 0, 2)
    _put(client, boss, slot_capacity=1)  # siết sức chứa: khung đã có 1 lịch xác nhận -> không xác nhận thêm
    full = client.post(f"/admin/appointments/{b}/confirm", headers=boss)
    assert full.status_code == 409 and "đủ chỗ" in full.json()["detail"]
    assert client.get("/admin/slots?date=bad", headers=boss).status_code == 422


# ---- hoàn thành lịch ----
def test_complete_confirmed_appointment(client, db_session, auth_on):
    boss = _manager_headers(client, db_session)
    _put(client, boss, slot_capacity=1)
    appt = _book("sess-done-000001", time(9, 0), "0912345661")
    assert client.post(f"/admin/appointments/{appt}/complete", headers=boss).status_code == 409  # chưa xác nhận
    assert client.post(f"/admin/appointments/{appt}/confirm", headers=boss).status_code == 200
    assert _book_as("sess-done-000002", time(9, 0), "0912345662").status == "SLOT_FULL"
    assert client.post(f"/admin/appointments/{appt}/complete", headers=boss).json() == {"ok": True, "status": "DONE"}
    with SessionLocal() as db:
        row = db.get(Appointment, appt)
        assert row.status == "DONE" and row.slot_id is not None and db.get(AppointmentSlot, row.slot_id).booked_count == 0
        assert "đã hoàn thành" in db.query(Message).filter_by(session_id="sess-done-000001", role="system").order_by(Message.id.desc()).first().content
    assert _book_as("sess-done-000002", time(9, 0), "0912345662").ok  # xong việc thì khung giờ trống lại
    assert client.post(f"/admin/appointments/{appt}/complete", headers=boss).status_code == 409  # không hoàn thành 2 lần
    assert client.post("/admin/appointments/999999/complete", headers=boss).status_code == 404
    assert not DbBookingService().cancel_appointment("sess-done-000001", appt).ok  # lịch DONE không hủy/đổi được
    assert not DbBookingService().reschedule_appointment("sess-done-000001", appt, DAY, time(10, 0)).ok
