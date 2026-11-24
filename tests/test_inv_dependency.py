"""L3 (Đợt 3B): task dependency (depends_on) chạy qua /chat + graph thật + Postgres, LLM thay bằng FakeLLM.

Mục tiêu: chứng minh "nếu có xe A thì đặt lịch / chốt mua" chỉ chạy writer KHI điều kiện được xác minh bằng kết quả
tool của Sales, và không bao giờ ghi DB / báo thành công giả khi điều kiện sai. FakeLLM chỉ giả "LLM hiểu gì"
(depends_on do preprocess trả về); toàn bộ logic coordinator, coordinator_resume, sales -> task_results, validation,
output guard là code thật. Việc LLM thật có gắn depends_on đúng hay không là việc của L5 (ca DP_*).

ID kịch bản: DP01-DP14 (xem kế hoạch, mục C3).
"""
import pytest

from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.session import SessionLocal
from app.services import get_services
from tests.conversation_helpers import Conversation, unique_phone
from tests.fake_llm import fake_llm, slot, task  # noqa: F401 - fixture

MODEL = "FOTON AUMARK-9T (3800)"       # có đúng một dòng khớp tên đầy đủ, giá 555.000.000
MODEL_PRICE = "555.000.000"
OTHER_MODEL = "FUSO FI170"             # khớp đúng, nhưng find_model cũng trả FI170L (anh em)
PARTIAL_MODEL = "FOTON AUMARK-9T"      # không khớp đúng tên nào, có nhiều mẫu gần giống
MISSING_MODEL = "Tesla Cybertruck"     # không có trong catalog


@pytest.fixture(autouse=True)
def clean_bookings():
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


# ---------------------------------------------------------------------------------------------------------------
# Dựng kịch bản
# ---------------------------------------------------------------------------------------------------------------
def visit_slots(phone: str):
    return [slot("service_type", "SHOWROOM_VISIT", "xem xe"),
            slot("appointment_when", "ngày mai lúc 9 giờ sáng", "ngày mai lúc 9 giờ sáng"),
            slot("customer_name", "Nguyễn Văn An", "Nguyễn Văn An"),
            slot("customer_phone", phone, phone)]


def conditional_visit(fake_llm, models: list[str], phone: str, *, found_text: str = "Mẫu xe này hiện có tại showroom ạ.",
                      sales_final: str | None = None, tag_car_model: bool = True) -> tuple[str, list]:
    """'Nếu có <models> thì đặt lịch xem xe': mỗi mẫu một task SALES, một task APPOINTMENT phụ thuộc tất cả.
    tag_car_model=False: task SALES không mang car_model, nên tên xe của lịch chỉ có thể đến từ kết quả tool đã xác minh."""
    text = f"Nếu có {' và '.join(models)} thì đặt lịch xem xe ngày mai lúc 9 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    lookups = [task("SALES", "product_detail", f"tìm {m}", car_model=m if tag_car_model else None) for m in models]
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch xem xe", depends_on=[t.task_id for t in lookups])
    fake_llm.understands(text, *lookups, visit, slots=visit_slots(phone))
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": m}) for m in models]],
                        final=sales_final or found_text)
    return text, lookups


def say(client, name: str, text: str) -> dict:
    return Conversation(client, name).say(text)


# ===== DP01: điều kiện đúng -> Sales chạy trước, rồi appointment đề xuất lịch đúng xe =====
def test_dp01_condition_met_runs_sales_then_appointment_with_verified_vehicle(client, fake_llm):
    conv = Conversation(client, "dp01")
    text, _ = conditional_visit(fake_llm, [MODEL], unique_phone(), tag_car_model=False)
    result = conv.say(text)
    trace = result["trace"]
    assert trace.index("sales") < trace.index("coordinator_resume") < trace.index("appointment")
    assert conv.appointments() == []                       # mới đề xuất, chưa xác nhận -> chưa ghi DB (OI04)
    assert "xác nhận" in result["reply"].lower()
    workflow = conv.state.get("active_workflow")
    assert workflow is not None and workflow.workflow_type == "appointment"
    assert MODEL in result["reply"]                        # xe do tool xác minh đi vào lịch đề xuất


# ===== DP02: điều kiện sai (xe không có) -> writer KHÔNG chạy, DB không đổi, không báo đã đặt =====
def test_dp02_condition_not_met_never_runs_the_writer(client, fake_llm):
    conv = Conversation(client, "dp02")
    text, _ = conditional_visit(fake_llm, [MISSING_MODEL], unique_phone(),
                                sales_final="Hiện bên mình chưa có mẫu Tesla Cybertruck ạ.")
    result = conv.say(text)
    assert "sales" in result["trace"] and "appointment" not in result["trace"]
    assert conv.appointments() == []
    assert "đã đặt lịch" not in result["reply"].lower() and "ghi nhận lịch hẹn" not in result["reply"].lower()
    assert "chưa có" in result["reply"].lower()            # khách vẫn nhận được câu trả lời của phần tìm xe
    assert conv.state.get("waiting_tasks") in ([], None)  # không còn task treo


# ===== DP03: xe gần giống (không khớp đúng tên) -> hỏi lại, không ghi DB =====
def test_dp03_similar_model_asks_instead_of_booking(client, fake_llm):
    conv = Conversation(client, "dp03")
    text, _ = conditional_visit(fake_llm, [PARTIAL_MODEL], unique_phone(), sales_final="Bên mình có các mẫu Aumark 9T ạ.")
    result = conv.say(text)
    assert "appointment" not in result["trace"] and conv.appointments() == []
    assert "Mẫu gần nhất" in result["reply"] and "AUMARK-9T" in result["reply"]
    assert "đặt lịch xem" in result["reply"].lower()       # câu hỏi 'có muốn đặt lịch xem mẫu này không'


# ===== DP04: prerequisite trả nhiều mẫu, khách chưa chọn -> hỏi chọn, writer không chạy =====
def test_dp04_several_matches_without_a_chosen_model_asks_which_one(client, fake_llm):
    conv = Conversation(client, "dp04")
    phone = unique_phone()
    text = f"Nếu có xe Aumark thì đặt lịch xem xe ngày mai lúc 9 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    lookup = task("SALES", "search_catalog", "tìm xe Aumark")                    # không có car_model cụ thể
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch xem xe", depends_on=[lookup.task_id])
    fake_llm.understands(text, lookup, visit, slots=visit_slots(phone))
    fake_llm.uses_tools("sales", [[("search_products", {"query": "Aumark", "limit": 3})]], final="Có các mẫu Aumark ạ.")
    result = conv.say(text)
    assert "appointment" not in result["trace"] and conv.appointments() == []
    assert "chọn" in result["reply"].lower() and "AUMARK" in result["reply"]


# ===== DP05: 'nếu cả hai đều có' - một mẫu có, một mẫu không -> writer không chạy =====
def test_dp05_both_required_but_only_one_exists_blocks_the_writer(client, fake_llm):
    conv = Conversation(client, "dp05")
    text, _ = conditional_visit(fake_llm, [MODEL, MISSING_MODEL], unique_phone(),
                                sales_final="Có Aumark 9T, chưa có Tesla Cybertruck ạ.")
    result = conv.say(text)
    assert "sales" in result["trace"] and "appointment" not in result["trace"]
    assert conv.appointments() == []


# ===== DP06: 'nếu cả hai đều có' - đủ cả hai -> writer chạy, lịch ghi cả hai mẫu =====
def test_dp06_both_required_and_both_exist_runs_the_writer_once(client, fake_llm):
    conv = Conversation(client, "dp06")
    text, _ = conditional_visit(fake_llm, [MODEL, OTHER_MODEL], unique_phone())
    result = conv.say(text)
    assert result["trace"].count("appointment") == 1 and "coordinator_resume" in result["trace"]
    assert conv.appointments() == []                       # vẫn chờ khách xác nhận
    assert MODEL in result["reply"] and OTHER_MODEL in result["reply"]


# ===== DP07: Sales lỗi tool -> không coi là 'có xe' =====
def test_dp07_sales_tool_error_does_not_release_the_writer(client, fake_llm, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("catalog down")
    monkeypatch.setattr(get_services().product, "find_model", broken)
    conv = Conversation(client, "dp07")
    text, _ = conditional_visit(fake_llm, [MODEL], unique_phone())
    result = conv.say(text)
    assert "appointment" not in result["trace"] and conv.appointments() == []
    assert "đã đặt lịch" not in result["reply"].lower()


# ===== DP08: dependency + writer thứ hai -> vẫn tối đa một writer, writer hoãn không mất (OI02/OI03) =====
def test_dp08_dependent_writer_plus_second_writer_keeps_single_writer_rule(client, fake_llm):
    conv = Conversation(client, "dp08")                    # say() assert tối đa một writer mỗi lượt
    phone = unique_phone()
    text = f"Nếu có {MODEL} thì đặt lịch xem xe ngày mai lúc 9 giờ sáng và mình muốn làm hợp đồng mua xe luôn, SĐT {phone}"
    lookup = task("SALES", "product_detail", f"tìm {MODEL}", car_model=MODEL)
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch xem xe", depends_on=[lookup.task_id])
    buy = task("CONTRACT", "start_contract", "chốt mua")
    fake_llm.understands(text, lookup, visit, buy, slots=visit_slots(phone)[:2] + [visit_slots(phone)[3]])
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": MODEL})]], final="Mẫu này có ạ.")
    result = conv.say(text)
    writers = [n for n in result["trace"] if n in ("appointment", "contract")]
    assert len(writers) == 1
    assert "hoàn tất hợp đồng" in result["reply"]           # ghi chú hoãn của writer thứ hai (DEFERRED_NOTE)


# ===== DP09: writer chạy sau điều kiện vẫn cần khách xác nhận; xác nhận ở lượt sau ghi đúng một lịch =====
def test_dp09_dependent_booking_still_needs_confirmation_then_writes_once(client, fake_llm):
    conv = Conversation(client, "dp09")
    text, _ = conditional_visit(fake_llm, [MODEL], unique_phone(), tag_car_model=False)
    first = conv.say(text)
    assert "appointment" in first["trace"] and conv.appointments() == []

    fake_llm.understands("Đúng rồi", task("APPOINTMENT", "book_appointment", "đồng ý", "ACCEPT_OFFER"),
                         accepts_offer=True)
    second = conv.say("Đúng rồi")
    [row] = conv.appointments()
    assert MODEL in (row.note or "")                        # xe đã xác minh nằm trong ghi chú lịch hẹn
    assert row.service_type == "SHOWROOM_VISIT" and row.status == "CONFIRMED" and row.start_time.hour == 9
    assert "coordinator_resume" not in second["trace"]      # lượt xác nhận không phải lượt có dependency


# ===== DP10: hai ý ĐỘC LẬP (không depends_on) -> như hành vi cũ, không qua coordinator_resume =====
def test_dp10_independent_tasks_do_not_wait_for_each_other(client, fake_llm):
    conv = Conversation(client, "dp10")
    phone = unique_phone()
    text = f"Giá {MODEL} bao nhiêu, cho mình qua showroom xem xe ngày mai lúc 9 giờ sáng, tên Nguyễn Văn An, SĐT {phone}"
    fake_llm.understands(text, task("SALES", "product_detail", f"giá {MODEL}", car_model=MODEL),
                         task("APPOINTMENT", "book_appointment", "qua showroom xem xe"), slots=visit_slots(phone))
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": MODEL})]],
                        final=f"{MODEL} giá {MODEL_PRICE} đồng ạ.")
    result = conv.say(text)
    assert {"sales", "appointment"} <= set(result["trace"]) and "coordinator_resume" not in result["trace"]
    assert MODEL_PRICE in result["reply"]


# ===== DP11: giá do Sales trả ở phase 1 vẫn được Output Guard cho phép ở bản ghép (OI07) =====
def test_dp11_prices_from_the_prerequisite_phase_pass_the_output_guard(client, fake_llm):
    conv = Conversation(client, "dp11")
    text, _ = conditional_visit(fake_llm, [MODEL], unique_phone(), sales_final=f"{MODEL} giá {MODEL_PRICE} đồng ạ.")
    result = conv.say(text)
    assert "human_handover" not in result["trace"] and "appointment" in result["trace"]
    assert MODEL_PRICE in result["reply"]


# ===== DP12: giá bịa ở phase 1 bị chặn, writer của phase 2 không được báo thành công (OI07 + OI05) =====
def test_dp12_wrong_price_in_prerequisite_blocks_the_whole_draft(client, fake_llm):
    conv = Conversation(client, "dp12")
    text, _ = conditional_visit(fake_llm, [MODEL], unique_phone(), sales_final=f"{MODEL} giá 500.000.000 đồng ạ.")
    result = conv.say(text)
    assert "human_handover" in result["trace"]
    assert "500.000.000" not in result["reply"] and conv.appointments() == []


# ===== DP13: dependency cho hợp đồng - điều kiện đúng -> contract nhận xe đã xác minh =====
def test_dp13_contract_depends_on_a_found_model(client, fake_llm):
    conv = Conversation(client, "dp13")
    text = f"Nếu có {MODEL} thì mình muốn làm hợp đồng mua xe luôn"
    lookup = task("SALES", "product_detail", f"tìm {MODEL}")            # không car_model: xe chỉ đến từ tool
    buy = task("CONTRACT", "start_contract", "chốt mua", depends_on=[lookup.task_id])
    fake_llm.understands(text, lookup, buy)
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": MODEL})]], final="Mẫu này có ạ.")
    result = conv.say(text)
    assert result["trace"].index("sales") < result["trace"].index("coordinator_resume") < result["trace"].index("contract")
    car = conv.state["known_facts"]["car_model"]
    assert car.value == MODEL                                # xe do hệ thống xác minh, không phải lời LLM
    assert "Họ và tên" in result["reply"] or "họ" in result["reply"].lower()   # hỏi nốt thông tin hợp đồng


# ===== DP14: dependency cho hợp đồng - điều kiện sai -> contract KHÔNG chạy =====
def test_dp14_contract_does_not_start_when_the_model_is_missing(client, fake_llm):
    conv = Conversation(client, "dp14")
    text = f"Nếu có {MISSING_MODEL} thì mình muốn làm hợp đồng mua xe luôn"
    lookup = task("SALES", "product_detail", f"tìm {MISSING_MODEL}", car_model=MISSING_MODEL)
    buy = task("CONTRACT", "start_contract", "chốt mua", depends_on=[lookup.task_id])
    fake_llm.understands(text, lookup, buy)
    fake_llm.uses_tools("sales", [[("find_by_model_name", {"model_name": MISSING_MODEL})]],
                        final="Bên mình chưa có mẫu Tesla Cybertruck ạ.")
    result = conv.say(text)
    assert "contract" not in result["trace"]
    workflow = conv.state.get("active_workflow")
    assert not (workflow and workflow.workflow_type == "contract")
