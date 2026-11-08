"""Bộ phân tích tin nhân viên + hộp thoại duyệt: đề xuất đúng, chỉ ghi khi duyệt, bot nhìn thấy kết quả."""
from datetime import date, time, timedelta

from app.agent.date_extraction import resolve
from app.agent.graph import get_graph
from app.db.models import Appointment, Customer, HandoverTicket, Message, StaffAction
from app.db.session import SessionLocal
from app.services.booking import DbBookingService
from app.services.staff_actions import _rule_date, _rule_drafts, _rule_price, _rule_time, normalize_params

TODAY = date.today()
LATER = TODAY + timedelta(days=12)
LATER_TXT = f"{LATER:%d/%m/%Y}"


def _chat_with_staff(client, sid, *staff_lines):
    """Khách nhắn (để phiên có checkpoint của bot) -> nhân viên vào chat và nhắn các dòng staff_lines."""
    assert client.post("/chat", json={"session_id": sid, "message": "Xin chào, mình cần hỗ trợ"}).status_code == 200
    ticket_id = client.post(f"/admin/conversations/{sid}/takeover").json()["ticket_id"]
    for line in staff_lines:
        assert client.post(f"/admin/tasks/{ticket_id}/reply", json={"content": line}).status_code == 200
    return ticket_id


def _scan(client, ticket_id):
    r = client.post(f"/admin/tasks/{ticket_id}/actions/scan")
    assert r.status_code == 200, r.text
    return r.json()


def _by_type(data):
    return {a["type"]: a for a in data["actions"]}


def _facts(sid):
    return get_graph().get_state({"configurable": {"thread_id": sid}}).values["known_facts"]


# ---- bộ phân tích (không qua DB) ----------------------------------------------------------------------------------
def test_rule_parsers():
    today = date(2026, 11, 8)  # Chủ nhật
    assert resolve(_rule_date("hẹn thứ 7 tuần sau nhé", today), today) == date(2026, 11, 14)
    assert resolve(_rule_date("ngày mai bạn qua", today), today) == date(2026, 11, 9)
    assert resolve(_rule_date("lịch 15/12", today), today) == date(2026, 12, 15)
    assert resolve(_rule_date("ngày 3 tháng 12", today), today) == date(2026, 12, 3)
    assert _rule_date("hẹn bạn sớm", today) is None
    assert _rule_time("9h sáng") == "09:00" and _rule_time("2 giờ chiều") == "14:00" and _rule_time("14h30") == "14:30"
    assert _rule_time("ngày 15/12") is None and _rule_time("không có giờ") is None
    assert _rule_price("mình chốt 780 triệu nhé") == 780_000_000
    assert _rule_price("chốt giá 1,2 tỷ") == 1_200_000_000
    assert _rule_price("thống nhất 780.000.000 đồng") == 780_000_000
    assert _rule_price("giảm 20 triệu cho bạn") is None  # mới giảm, chưa có giá cuối -> không phải giá chốt


def test_rules_each_intent_and_normalization():
    appts = [{"id": 7, "service_type": "MAINTENANCE", "appointment_date": "2026-12-01", "start_time": "09:00",
              "status": "CONFIRMED"}]
    cases = {
        "Mình đặt lịch bảo dưỡng ngày 15/12/2030 lúc 9h cho bạn, SĐT 0912345678": "BOOK_APPOINTMENT",
        "Mình đổi lịch sang 16/12/2030 lúc 10h nhé": "RESCHEDULE_APPOINTMENT",
        "Mình hủy lịch hẹn giúp bạn nhé": "CANCEL_APPOINTMENT",
        "Hai bên chốt 780 triệu cho Fuso Canter": "AGREED_PRICE",
        "Địa chỉ là 12 Nguyễn Trãi, Hà Nội": "CUSTOMER_INFO",
        "Ghi chú: khách ưu tiên xe màu trắng": "HANDLING_NOTE",
    }
    for text, expected in cases.items():
        drafts = [d for _, d in _rule_drafts([(1, "staff", text)], appts)]
        assert expected in {d.type for d in drafts}, text
    book = next(d for _, d in _rule_drafts([(1, "staff", "Đặt lịch bảo dưỡng ngày 15/12/2030 lúc 9h, SĐT 0912345678")], appts) if d.type == "BOOK_APPOINTMENT")
    params, warnings = normalize_params("BOOK_APPOINTMENT", {"service_type": book.service_type, "date": "2030-12-15",
                                                              "time": "09:00", "phone": book.customer_phone}, appts)
    assert params == {"service_type": "MAINTENANCE", "date": "2030-12-15", "time": "09:00", "phone": "+84912345678"}
    assert not warnings


def test_normalize_rejects_bad_values():
    appts = [{"id": 7}]
    _, w = normalize_params("CANCEL_APPOINTMENT", {"appointment_id": 99}, appts + [{"id": 8}])
    assert any("không thuộc" in x for x in w)  # mã lịch lạ (LLM bịa) bị loại
    p, _ = normalize_params("CANCEL_APPOINTMENT", {"appointment_id": None}, appts)
    assert p["appointment_id"] == 7  # chỉ một lịch -> chắc chắn là lịch đó
    _, w = normalize_params("RESCHEDULE_APPOINTMENT", {"appointment_id": 7, "date": "2020-01-01"}, appts)
    assert any("đã qua" in x for x in w)
    _, w = normalize_params("AGREED_PRICE", {"price_vnd": 780}, appts)
    assert w  # 780 (nhầm đơn vị) không được ghi như giá chốt
    _, w = normalize_params("CUSTOMER_INFO", {"phone": "123"}, appts)
    assert any("không hợp lệ" in x for x in w)


# ---- API ----------------------------------------------------------------------------------------------------------
def test_scan_proposes_but_changes_nothing_and_is_a_snapshot(client, db_session):
    tid = _chat_with_staff(client, "sess-sa-000001",
                           f"Mình đặt lịch bảo dưỡng ngày {LATER_TXT} lúc 9h cho bạn nhé, SĐT 0912345601",
                           "Hai bên chốt 780 triệu cho Fuso Canter nhé")
    assert client.get(f"/admin/tasks/{tid}/actions").json()["unscanned_staff"] == 2  # banner nhắc, chưa tốn LLM
    data = _scan(client, tid)
    kinds = _by_type(data)
    assert {"BOOK_APPOINTMENT", "AGREED_PRICE"} <= set(kinds)
    assert kinds["BOOK_APPOINTMENT"]["params"]["date"] == LATER.isoformat()
    assert kinds["AGREED_PRICE"]["params"]["price_vnd"] == 780_000_000
    assert data["unscanned_staff"] == 0
    with SessionLocal() as db:  # đề xuất chưa làm thay đổi dữ liệu nào
        assert db.query(Appointment).count() == 0
    again = _scan(client, tid)  # không có tin mới: giữ nguyên các thẻ (không tính lại)
    assert [a["id"] for a in again["actions"]] == [a["id"] for a in data["actions"]]
    client.post(f"/admin/tasks/{tid}/reply", json={"content": "Ghi chú: khách ưu tiên xe màu trắng"})
    assert client.get(f"/admin/tasks/{tid}/actions").json()["unscanned_staff"] == 1
    final = _scan(client, tid)  # có tin mới: tổng hợp lại cả đợt, thay các thẻ chưa duyệt, mỗi ý định một thẻ
    assert sorted(a["type"] for a in final["actions"]) == ["AGREED_PRICE", "BOOK_APPOINTMENT", "HANDLING_NOTE"]
    with SessionLocal() as db:
        assert db.query(StaffAction).filter_by(status="PROPOSED").count() == 3  # không dồn thẻ cũ


def test_apply_booking_is_visible_to_bot(client, db_session):
    """Ca thật: nhân viên nhắn đặt lịch bảo dưỡng -> sau khi duyệt, bot đọc được lịch (nguồn sự thật là DB)."""
    sid = "sess-sa-000002"
    tid = _chat_with_staff(client, sid, f"Mình đặt lịch bảo dưỡng ngày {LATER_TXT} lúc 9h cho bạn nhé, SĐT 0912345602")
    book = _by_type(_scan(client, tid))["BOOK_APPOINTMENT"]
    assert DbBookingService().list_appointments(sid) == []
    r = client.post(f"/admin/staff-actions/{book['id']}/apply")
    assert r.status_code == 200 and "xác nhận" in r.json()["message"]
    seen = DbBookingService().list_appointments(sid, active_only=True)
    assert len(seen) == 1 and seen[0].status == "CONFIRMED" and seen[0].appointment_date == LATER.isoformat()
    assert seen[0].start_time == "09:00" and seen[0].service_type == "MAINTENANCE"
    with SessionLocal() as db:
        notice = db.query(Message).filter_by(session_id=sid, role="system").order_by(Message.id.desc()).first()
        assert "đã được nhân viên đặt" in notice.content  # khách được báo trong chat
        row = db.get(StaffAction, book["id"])
        assert row.status == "APPLIED" and row.result and row.decided_at
    assert client.post(f"/admin/staff-actions/{book['id']}/apply").status_code == 409  # không áp dụng hai lần
    assert client.get(f"/admin/tasks/{tid}/actions").json()["actions"] == []


def test_apply_with_edited_params_and_failure_keeps_proposal(client, db_session):
    tid = _chat_with_staff(client, "sess-sa-000003", f"Đặt lịch sửa chữa ngày {LATER_TXT} lúc 10h cho bạn")
    book = _by_type(_scan(client, tid))["BOOK_APPOINTMENT"]
    assert any("điện thoại" in w for w in book["warnings"])  # khách chưa có SĐT -> hộp thoại bắt nhập
    r = client.post(f"/admin/staff-actions/{book['id']}/apply")
    assert r.status_code == 422 and "điện thoại" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.get(StaffAction, book["id"]).status == "PROPOSED" and db.query(Appointment).count() == 0
    edited = {**book["params"], "phone": "0912345603", "time": "11:00"}
    r = client.post(f"/admin/staff-actions/{book['id']}/apply", json={"params": edited})
    assert r.status_code == 200
    with SessionLocal() as db:
        appt = db.query(Appointment).one()
        assert appt.start_time == time(11, 0) and appt.contact_phone == "+84912345603"  # giá trị nhân viên sửa thắng
        assert db.get(StaffAction, book["id"]).params["time"] == "11:00"


def test_apply_cancel_and_reschedule_only_touch_this_session(client, db_session):
    sid, other = "sess-sa-000004", "sess-sa-000005"
    from app.contracts.schemas import ServiceType
    mine = DbBookingService().create_appointment(sid, ServiceType.MAINTENANCE, LATER, time(9, 0), "0912345604").appointment_id
    theirs = DbBookingService().create_appointment(other, ServiceType.MAINTENANCE, LATER, time(9, 0), "0912345605").appointment_id
    tid = _chat_with_staff(client, sid, f"Mình đổi lịch sang {(LATER + timedelta(days=1)):%d/%m/%Y} lúc 10h nhé")
    resched = _by_type(_scan(client, tid))["RESCHEDULE_APPOINTMENT"]
    assert resched["params"]["appointment_id"] == mine
    # nhân viên (hoặc LLM bịa) trỏ sang lịch của khách khác -> bị chặn
    r = client.post(f"/admin/staff-actions/{resched['id']}/apply", json={"params": {**resched["params"], "appointment_id": theirs}})
    assert r.status_code == 422 and "không thuộc" in r.json()["detail"]
    assert client.post(f"/admin/staff-actions/{resched['id']}/apply").status_code == 200
    with SessionLocal() as db:
        assert db.get(Appointment, mine).appointment_date == LATER + timedelta(days=1)
        assert db.get(Appointment, theirs).appointment_date == LATER

    client.post(f"/admin/tasks/{tid}/reply", json={"content": "Xin lỗi, mình hủy lịch hẹn giúp bạn nhé"})
    cancel = _by_type(_scan(client, tid))["CANCEL_APPOINTMENT"]
    assert client.post(f"/admin/staff-actions/{cancel['id']}/apply").status_code == 200
    with SessionLocal() as db:
        assert db.get(Appointment, mine).status == "CANCELLED" and db.get(Appointment, theirs).status == "PENDING_APPROVAL"


def test_apply_price_customer_and_note_reach_bot_state(client, db_session):
    sid = "sess-sa-000006"
    tid = _chat_with_staff(client, sid, "Hai bên chốt 780 triệu cho Fuso Canter nhé",
                           "Tên khách là Nguyễn Văn An, SĐT 0912345606, địa chỉ là 12 Nguyễn Trãi, Hà Nội",
                           "Ghi chú: đã hứa tặng thảm lót sàn")
    actions = _scan(client, tid)["actions"]
    for a in actions:
        assert client.post(f"/admin/staff-actions/{a['id']}/apply").status_code == 200, a
    facts = _facts(sid)
    val = lambda k: getattr(facts[k], "value", None) if not isinstance(facts[k], dict) else facts[k]["value"]
    assert val("decided_price") == 780_000_000
    assert val("customer_name") == "Nguyễn Văn An" and val("customer_phone") == "+84912345606"
    assert "12 Nguyễn Trãi" in val("customer_address")
    assert val("staff_notes") == ["[Khác] đã hứa tặng thảm lót sàn"]
    with SessionLocal() as db:
        customer = db.query(Customer).one()
        assert customer.phone_number == "+84912345606" and customer.full_name == "Nguyễn Văn An"
        summary = db.get(HandoverTicket, tid).summary
        assert "[Giá chốt] 780.000.000 đồng" in summary and "[Ghi chú NV · Khác]" in summary


def test_dismiss_and_permissions(client, db_session):
    tid = _chat_with_staff(client, "sess-sa-000007", "Ghi chú: gọi lại khách chiều nay")
    note = _by_type(_scan(client, tid))["HANDLING_NOTE"]
    assert client.post(f"/admin/staff-actions/{note['id']}/dismiss").status_code == 200
    assert client.post(f"/admin/staff-actions/{note['id']}/apply").status_code == 409  # đã bỏ
    assert client.get(f"/admin/tasks/{tid}/actions").json()["actions"] == []
    assert _scan(client, tid)["actions"] == []  # không đề xuất lại điều đã bỏ
    assert client.post("/admin/staff-actions/999999/apply").status_code == 404
    assert client.post("/admin/tasks/999999/actions/scan").status_code == 404


# ---- vào lại chat sau khi kết thúc (ticket mới) -------------------------------------------------------------------
def _reenter(client, sid, old_ticket):
    assert client.post(f"/admin/tasks/{old_ticket}/resolve").status_code == 200
    new = client.post(f"/admin/conversations/{sid}/takeover").json()["ticket_id"]
    assert new != old_ticket
    return new


def test_reentering_chat_does_not_reask_approved_or_dismissed(client, db_session):
    sid = "sess-sa-000008"
    t1 = _chat_with_staff(client, sid, "Ghi chú: khách VIP", "Hai bên chốt 780 triệu cho Fuso Canter nhé")
    acts = _by_type(_scan(client, t1))
    assert client.post(f"/admin/staff-actions/{acts['HANDLING_NOTE']['id']}/apply").status_code == 200  # đã duyệt
    assert client.post(f"/admin/staff-actions/{acts['AGREED_PRICE']['id']}/dismiss").status_code == 200  # đã bỏ
    t2 = _reenter(client, sid, t1)
    assert _scan(client, t2)["actions"] == []  # trước đây: tin cũ bị phân tích lại và hỏi lại cả hai mục
    client.post(f"/admin/tasks/{t2}/reply", json={"content": "Ghi chú: gọi lại khách sáng mai"})
    assert [a["type"] for a in _scan(client, t2)["actions"]] == ["HANDLING_NOTE"]  # chỉ tin mới
    assert len(client.get(f"/admin/tasks/{t2}/actions").json()["actions"]) == 1


def test_undecided_proposals_follow_the_session_and_dismiss_all(client, db_session):
    sid = "sess-sa-000009"
    t1 = _chat_with_staff(client, sid, "Ghi chú: gọi lại khách chiều nay")
    assert len(_scan(client, t1)["actions"]) == 1
    t2 = _reenter(client, sid, t1)
    assert len(client.get(f"/admin/tasks/{t2}/actions").json()["actions"]) == 1  # chưa quyết thì vẫn còn, không mất
    assert client.post(f"/admin/tasks/{t2}/actions/dismiss-all").json()["dismissed"] == 1
    assert client.get(f"/admin/tasks/{t2}/actions").json()["actions"] == []
    t3 = _reenter(client, sid, t2)
    assert _scan(client, t3)["actions"] == []


def test_legacy_duplicate_proposals_are_hidden(client, db_session):
    """Mục trùng do bản cũ phân tích lại tin cũ (đã có dòng APPLIED giống hệt) không hiện lại trong hộp thoại."""
    sid = "sess-sa-000010"
    t1 = _chat_with_staff(client, sid, "Ghi chú: khách VIP")
    note = _by_type(_scan(client, t1))["HANDLING_NOTE"]
    assert client.post(f"/admin/staff-actions/{note['id']}/apply").status_code == 200
    with SessionLocal() as db:
        done = db.get(StaffAction, note["id"])
        db.add(StaffAction(ticket_id=t1, session_id=sid, action_type="HANDLING_NOTE", params=done.params,
                           summary=done.summary))
        db.commit()
    assert client.get(f"/admin/tasks/{t1}/actions").json()["actions"] == []
    with SessionLocal() as db:
        assert db.query(StaffAction).filter_by(session_id=sid, status="DISMISSED").count() == 1


# ---- khách nhắn thông tin SAU tin nhân viên (ca thật: nhân viên đặt lịch "ngày mai", khách nhắn tên/SĐT/giờ) ----------
def _customer_says(client, sid, *lines):
    for line in lines:  # phiên đang do nhân viên xử lý: tin khách chỉ được lưu, bot im lặng
        assert client.post("/chat", json={"session_id": sid, "message": line}).json()["reply"] == ""


def test_customer_details_after_staff_message_fill_the_booking(client, db_session):
    sid = "sess-sa-000011"
    tid = _chat_with_staff(client, sid, "anh ơi em đặt lịch bảo dưỡng cho anh vào ngày mai nha")
    first = _by_type(_scan(client, tid))["BOOK_APPOINTMENT"]  # quét sớm: mới biết ngày
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert first["params"]["date"] == tomorrow and "phone" not in first["params"] and "time" not in first["params"]
    _customer_says(client, sid, "ok em", "anh tên nguyễn văn ba", "sđt 0929394948", "mai 9 giờ sáng anh đến nhé")
    client.post(f"/admin/tasks/{tid}/reply", json={"content": "dạ vâng ạ"})
    data = _scan(client, tid)
    book = _by_type(data)["BOOK_APPOINTMENT"]
    assert book["id"] != first["id"]  # tổng hợp lại cả đợt: thẻ cũ được thay bằng một thẻ đầy đủ
    assert book["params"] == {"service_type": "MAINTENANCE", "date": tomorrow, "time": "09:00", "phone": "+84929394948"}
    assert not book["warnings"]
    info = _by_type(data)["CUSTOMER_INFO"]
    assert info["params"] == {"name": "Nguyễn Văn Ba"}  # SĐT đã nằm trong lịch đặt nên không lặp lại
    assert len([a for a in data["actions"] if a["type"] == "BOOK_APPOINTMENT"]) == 1
    # duyệt cả hai: lịch được tạo đủ thông tin, hồ sơ khách có tên
    for a in data["actions"]:
        assert client.post(f"/admin/staff-actions/{a['id']}/apply").status_code == 200
    appt = DbBookingService().list_appointments(sid, active_only=True)[0]
    assert (appt.appointment_date, appt.start_time, appt.status) == (tomorrow, "09:00", "CONFIRMED")
    with SessionLocal() as db:
        assert db.query(Customer).one().full_name == "Nguyễn Văn Ba"
    assert _scan(client, tid)["actions"] == []  # không hỏi lại


def test_scan_picks_up_customer_messages_without_new_staff_reply(client, db_session):
    sid = "sess-sa-000012"
    tid = _chat_with_staff(client, sid, "Mình đặt lịch sửa chữa ngày mai cho bạn nhé")
    _scan(client, tid)
    _customer_says(client, sid, "mình là Trần Thị Mai, sđt 0911222333, 14h chiều mai nhé")
    book = _by_type(_scan(client, tid))["BOOK_APPOINTMENT"]
    assert book["params"]["time"] == "14:00" and book["params"]["phone"] == "+84911222333"


# ---- tổng hợp: mỗi ý định một thẻ, ghi chú gộp theo chủ đề (ca thương lượng giá) ----------------------------------
def _fake_llm(monkeypatch, *drafts):
    from app.services import staff_actions as svc
    monkeypatch.setattr(svc, "generate_structured", lambda *a, **k: svc.StaffPlan(actions=list(drafts)))
    return svc


def test_negotiation_is_one_note_not_one_per_message(client, db_session, monkeypatch):
    sid = "sess-sa-000013"
    tid = _chat_with_staff(client, sid, "ok anh, giá 700 triệu", "30% thôi ạ", "ok cho em mua xe nhé")
    svc = _fake_llm(
        monkeypatch,
        # LLM (hoặc bản nháp cũ) trả nhiều mảnh cho cùng một cuộc thương lượng: phải gộp
        svc_draft("AGREED_PRICE", price_vnd=650_000_000), svc_draft("AGREED_PRICE", price_vnd=700_000_000, car_model="Fuso Canter"),
        svc_draft("HANDLING_NOTE", note="Khách đề nghị giảm 50%.", note_topic="SALES_NEGOTIATION"),
        svc_draft("HANDLING_NOTE", note="Nhân viên chỉ giảm tối đa 30%.", note_topic="SALES_NEGOTIATION"),
        svc_draft("HANDLING_NOTE", note="Hai bên chốt 700 triệu, khách đồng ý mua.", note_topic="SALES_NEGOTIATION"),
        svc_draft("HANDLING_NOTE", note="Hứa tặng thảm lót sàn.", note_topic="GENERAL"))
    data = _scan(client, tid)
    assert sorted(a["type"] for a in data["actions"]) == ["AGREED_PRICE", "HANDLING_NOTE", "HANDLING_NOTE"]
    price = _by_type(data)["AGREED_PRICE"]["params"]
    assert price == {"price_vnd": 700_000_000, "car_model": "Fuso Canter"}  # giá cuối thắng
    notes = {a["params"]["topic"]: a["params"]["note"] for a in data["actions"] if a["type"] == "HANDLING_NOTE"}
    assert set(notes) == {"SALES_NEGOTIATION", "GENERAL"}  # mỗi chủ đề đúng một ghi chú
    assert all(s in notes["SALES_NEGOTIATION"] for s in ("giảm 50%", "tối đa 30%", "chốt 700 triệu"))


def test_decided_note_not_repeated_until_new_messages(client, db_session, monkeypatch):
    sid = "sess-sa-000014"
    tid = _chat_with_staff(client, sid, "Mình giảm cho bạn 30% nhé")
    _fake_llm(monkeypatch, svc_draft("HANDLING_NOTE", note="Đã giảm 30%.", note_topic="SALES_NEGOTIATION"))
    note = _scan(client, tid)["actions"][0]
    assert client.post(f"/admin/staff-actions/{note['id']}/apply").status_code == 200
    assert _scan(client, tid)["actions"] == []  # không có tin mới
    client.post(f"/admin/tasks/{tid}/reply", json={"content": "dạ vâng ạ"})  # có tin mới nhưng chưa có diễn biến mới
    assert _scan(client, tid)["actions"] == []  # LLM vẫn trả ghi chú cũ, mốc đã bao phủ nên bị loại
    _customer_says(client, sid, "cho mình thêm gói bảo hiểm miễn phí nhé")
    client.post(f"/admin/tasks/{tid}/reply", json={"content": "dạ bên em xin phép tặng thêm 1 năm bảo hiểm"})
    _fake_llm(monkeypatch, svc_draft("HANDLING_NOTE", note="Tặng thêm 1 năm bảo hiểm.", note_topic="SALES_NEGOTIATION"))
    again = _scan(client, tid)["actions"]
    assert len(again) == 1 and again[0]["params"]["note"] == "Tặng thêm 1 năm bảo hiểm."


def test_booking_and_negotiation_split_by_intent(client, db_session, monkeypatch):
    tid = _chat_with_staff(client, "sess-sa-000015", "đặt lịch bảo dưỡng ngày mai nhé", "giảm 10% cho bạn")
    from app.agent.date_extraction import DateInfo, Kind
    _fake_llm(monkeypatch,
              svc_draft("BOOK_APPOINTMENT", service_type="MAINTENANCE", date=DateInfo(kind=Kind.RELATIVE, offset=1),
                        time="09:00", customer_phone="0929394948"),
              svc_draft("HANDLING_NOTE", note="Giảm 10%.", note_topic="SALES_NEGOTIATION"))
    data = _scan(client, tid)
    assert sorted(a["type"] for a in data["actions"]) == ["BOOK_APPOINTMENT", "HANDLING_NOTE"]
    assert _by_type(data)["BOOK_APPOINTMENT"]["params"]["date"] == (TODAY + timedelta(days=1)).isoformat()


def svc_draft(type_, **kw):
    from app.services.staff_actions import ActionDraft
    return ActionDraft(type=type_, evidence=kw.pop("evidence", "x"), **kw)
