"""L1 (Đợt 3B): logic thuần của task dependency. Không gọi LLM, không ghi DB.

Phủ: _validate_task_dependencies (preprocess), router (không gộp task phụ thuộc), coordinator (chia phase, giới hạn
agent đọc, writer bị giữ/hoãn, dependency không thể thỏa), _prerequisite_status / _matched_vehicle /
coordinator_resume (khi nào được nhả writer). ID: DU01-DU20.
"""
from app.agent.nodes.coordinator import (DEFERRED_NOTE, MAX_READ_AGENTS, _matched_vehicle, _prerequisite_status,
                                         coordinate, coordinator, coordinator_resume, dispatch_resumed)
from app.agent.nodes.preprocess import _validate_task_dependencies
from app.agent.nodes.router import router
from app.contracts.schemas import ConversationInterpretation
from tests.fake_llm import task


def interpretation(*tasks) -> ConversationInterpretation:
    return ConversationInterpretation(message_summary="x", tasks=list(tasks))


def plan_item(agent: str, task_ids: list[str], depends_on: list[str] | None = None, order: int = 0) -> dict:
    return {"agent": agent, "text": agent, "order": order, "task_ids": task_ids, "depends_on": depends_on or []}


# ===== _validate_task_dependencies =====
def test_du01_valid_backward_dependency_is_kept():
    a = task("SALES", "product_detail", "tìm xe")
    b = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[a.task_id])
    out = _validate_task_dependencies(interpretation(a, b))
    assert out.tasks[1].depends_on == [a.task_id] and out.tasks[0].depends_on == []


def test_du02_self_forward_unknown_and_duplicate_references_are_discarded():
    a = task("SALES", "product_detail", "tìm xe")
    b = task("APPOINTMENT", "book_appointment", "đặt lịch")
    c = task("CONTRACT", "start_contract", "chốt")
    a2 = a.model_copy(update={"depends_on": [b.task_id]})                                    # tham chiếu tới task SAU
    b2 = b.model_copy(update={"depends_on": [b.task_id, "task_khong_ton_tai", a.task_id, a.task_id]})
    out = _validate_task_dependencies(interpretation(a2, b2, c))
    assert out.tasks[0].depends_on == []                  # không được phụ thuộc task đứng sau (chống vòng lặp)
    assert out.tasks[1].depends_on == [a.task_id]         # bỏ tự tham chiếu, ID lạ, trùng lặp
    assert out.tasks[2].depends_on == []


def test_du03_cycle_between_two_tasks_cannot_survive():
    a = task("SALES", "product_detail", "tìm xe")
    b = task("APPOINTMENT", "book_appointment", "đặt lịch")
    a2 = a.model_copy(update={"depends_on": [b.task_id]})
    b2 = b.model_copy(update={"depends_on": [a.task_id]})
    out = _validate_task_dependencies(interpretation(a2, b2))
    assert out.tasks[0].depends_on == [] and out.tasks[1].depends_on == [a.task_id]         # chỉ còn cạnh lùi


# ===== router =====
def routed(*tasks) -> list[dict]:
    return router({"user_text": "x", "conversation_interpretation": interpretation(*tasks)})["plan"]


def test_du04_independent_tasks_of_the_same_agent_are_still_merged():
    plan = routed(task("SALES", "a", "giá xe 1"), task("SALES", "b", "giá xe 2"))
    assert len(plan) == 1 and len(plan[0]["task_ids"]) == 2 and plan[0]["depends_on"] == []


def test_du05_dependent_task_is_never_merged_into_an_earlier_task_of_the_same_agent():
    a = task("SALES", "product_detail", "tìm xe A")
    b = task("SALES", "product_detail", "nếu có thì cho giá xe B", depends_on=[a.task_id])
    plan = routed(a, b)
    assert len(plan) == 2 and plan[1]["depends_on"] == [a.task_id]


def test_du06_task_ids_and_depends_on_are_copied_into_the_plan():
    a = task("SALES", "product_detail", "tìm xe")
    b = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[a.task_id])
    plan = routed(a, b)
    assert [p["agent"] for p in plan] == ["sales", "appointment"]
    assert plan[0]["task_ids"] == [a.task_id] and plan[1]["depends_on"] == [a.task_id]


# ===== coordinator (phase 1) =====
def first_phase(plan: list[dict], **state) -> dict:
    return coordinator({"session_id": "s", "plan": plan, **state})


def test_du07_dependent_writer_is_held_and_only_the_prerequisite_runs_first():
    out = first_phase([plan_item("sales", ["t1"]), plan_item("appointment", ["t2"], ["t1"], 1)])
    assert [i["agent"] for i in out["plan"]] == ["sales"]
    assert [i["agent"] for i in out["waiting_tasks"]] == ["appointment"]
    assert out["dependency_phase"] == "waiting_prerequisites"
    assert out["plan"][0]["dependency_task_ids"] == ["t1"]          # Sales biết phải trả task_results cho t1


def test_du08_independent_writer_runs_with_the_reads_as_before():
    out = first_phase([plan_item("sales", ["t1"]), plan_item("appointment", ["t2"], [], 1)])
    assert {i["agent"] for i in out["plan"]} == {"sales", "appointment"}
    assert out["waiting_tasks"] == [] and out["dependency_phase"] is None


def test_du09_a_prerequisite_is_not_dropped_by_the_read_agent_cap():
    reads = [plan_item("knowledge", ["k"], order=0), plan_item("after_sales", ["a"], order=1),
             plan_item("conversation", ["c"], order=2), plan_item("sales", ["t1"], order=3)]
    out = first_phase([*reads, plan_item("appointment", ["t2"], ["t1"], 4)])
    agents = [i["agent"] for i in out["plan"]]
    assert "sales" in agents and len(agents) <= MAX_READ_AGENTS     # prerequisite được ưu tiên, giới hạn vẫn giữ
    assert out["waiting_tasks"] and out["waiting_tasks"][0]["agent"] == "appointment"


def test_du10_second_writer_is_deferred_with_a_note_and_a_waiting_workflow():
    out = first_phase([plan_item("appointment", ["t1"], order=0), plan_item("contract", ["t2"], order=1)])
    assert [i["agent"] for i in out["plan"]] == ["appointment"]
    assert out["deferred_workflow"]["workflow_type"] == "contract"
    [note] = out["answers"]
    assert note["text"] == DEFERRED_NOTE.format(label="hoàn tất hợp đồng")


def test_du11_dependency_on_a_task_that_is_not_planned_is_refused_not_run_speculatively():
    out = first_phase([plan_item("appointment", ["t2"], ["t_missing"], 0)])
    assert out["plan"] == [] and out["waiting_tasks"] == [] and out["dependency_phase"] is None
    assert "chưa thể tiếp tục" in out["answers"][0]["text"]


def test_du12_empty_phase_goes_straight_to_validation():
    assert coordinate({"plan": []}) == "validation" and dispatch_resumed({"plan": []}) == "validation"


# ===== _prerequisite_status =====
def test_du13_prerequisite_status_from_structured_results():
    results = lambda status: {"task_results": [{"task_id": "t1", "status": status, "products": []}]}  # noqa: E731
    assert _prerequisite_status(results("found"), "t1", {"t2"}) == "success"
    for status in ("similar", "not_found", "failed"):
        assert _prerequisite_status(results(status), "t1", {"t2"}) == "failure"


def test_du14_prerequisite_without_result_is_pending_only_while_it_is_still_waiting():
    assert _prerequisite_status({}, "t1", {"t1"}) == "pending"
    assert _prerequisite_status({}, "t1", {"t2"}) == "failure"      # không ai sẽ trả kết quả cho nó -> không đợi mãi


def test_du15_writer_clarification_is_not_completion():
    a = task("APPOINTMENT", "book_appointment", "đặt lịch")
    state = {"conversation_interpretation": interpretation(a),
             "answers": [{"agent": "appointment", "text": "Bạn cho mình xin giờ nhé", "task_ids": [a.task_id],
                          "executed": False}]}
    assert _prerequisite_status(state, a.task_id, set()) == "failure"
    state["answers"][0]["executed"] = True
    assert _prerequisite_status(state, a.task_id, set()) == "success"


# ===== coordinator_resume (phase 2) =====
def resume(waiting: list[dict], results: list[dict], *tasks) -> dict:
    return coordinator_resume({"waiting_tasks": waiting, "task_results": results,
                               "conversation_interpretation": interpretation(*tasks)})


PRODUCT = {"id": 1, "name": "FOTON AUMARK-9T (3800)"}


def test_du16_found_releases_the_writer_with_the_verified_vehicle():
    look = task("SALES", "product_detail", "tìm", car_model="FOTON AUMARK-9T (3800)")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[look.task_id])
    out = resume([plan_item("appointment", [visit.task_id], [look.task_id], 1)],
                 [{"task_id": look.task_id, "agent": "sales", "status": "found", "products": [PRODUCT]}], look, visit)
    [released] = out["plan"]
    assert released["agent"] == "appointment" and released["verified_vehicle"] == PRODUCT["name"]
    assert released["depends_on"] == [] and out["waiting_tasks"] == []


def test_du17_not_found_and_failed_release_nothing():
    look = task("SALES", "product_detail", "tìm", car_model="Tesla Cybertruck")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[look.task_id])
    for status in ("not_found", "failed"):
        out = resume([plan_item("appointment", [visit.task_id], [look.task_id], 1)],
                     [{"task_id": look.task_id, "agent": "sales", "status": status, "products": []}], look, visit)
        assert out["plan"] == [] and out["waiting_tasks"] == [] and "answers" not in out


def test_du18_similar_asks_the_customer_for_appointment_but_never_books():
    look = task("SALES", "product_detail", "tìm", car_model="FOTON AUMARK-9T")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[look.task_id])
    out = resume([plan_item("appointment", [visit.task_id], [look.task_id], 1)],
                 [{"task_id": look.task_id, "agent": "sales", "status": "similar", "products": [PRODUCT]}], look, visit)
    assert out["plan"] == [] and "Mẫu gần nhất là FOTON AUMARK-9T (3800)" in out["answers"][0]["text"]


def test_du19_several_candidates_without_a_requested_model_ask_which_one():
    look = task("SALES", "search_catalog", "tìm xe Aumark")                 # không car_model
    for agent, word in (("appointment", "xem"), ("contract", "làm hợp đồng")):
        step = task(agent.upper(), "x", "bước sau", depends_on=[look.task_id])
        products = [PRODUCT, {"id": 2, "name": "FOTON AUMARK-9T (4500)"}]
        out = resume([plan_item(agent, [step.task_id], [look.task_id], 1)],
                     [{"task_id": look.task_id, "agent": "sales", "status": "found", "products": products}], look, step)
        assert out["plan"] == [] and "nhiều mẫu xe phù hợp" in out["answers"][0]["text"] and word in out["answers"][0]["text"]


def test_du20_a_prerequisite_still_pending_keeps_the_task_waiting():
    look = task("SALES", "product_detail", "tìm", car_model="X")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[look.task_id])
    waiting = [plan_item("sales", [look.task_id], [], 0), plan_item("appointment", [visit.task_id], [look.task_id], 1)]
    # t_look nằm trong waiting_ids và chưa có kết quả -> pending
    out = resume([waiting[1], waiting[0]], [], look, visit)
    assert [i["agent"] for i in out["waiting_tasks"]] == ["appointment"] and out["plan"][0]["agent"] == "sales"


def test_du21_two_prerequisites_with_one_car_each_release_the_writer_for_both_cars():
    a = task("SALES", "product_detail", "tìm A", car_model="FOTON AUMARK-9T (3800)")
    b = task("SALES", "product_detail", "tìm B", car_model="FUSO FI170")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[a.task_id, b.task_id])
    results = [{"task_id": a.task_id, "agent": "sales", "status": "found", "products": [PRODUCT]},
               {"task_id": b.task_id, "agent": "sales", "status": "found", "products": [{"id": 3, "name": "FUSO FI170"}]}]
    out = resume([plan_item("appointment", [visit.task_id], [a.task_id, b.task_id], 2)], results, a, b, visit)
    assert out["plan"][0]["verified_vehicle"] == "FOTON AUMARK-9T (3800), FUSO FI170"


def test_du22_matched_vehicle_requires_an_exact_name_when_a_model_was_requested():
    look = task("SALES", "product_detail", "tìm", car_model="FUSO FI170")
    visit = task("APPOINTMENT", "book_appointment", "đặt lịch", depends_on=[look.task_id])
    state = {"conversation_interpretation": interpretation(look, visit),
             "task_results": [{"task_id": look.task_id, "status": "found",
                               "products": [{"id": 1, "name": "FUSO FI170L"}, {"id": 2, "name": "FUSO FI170"}]}]}
    vehicle, names = _matched_vehicle(state, plan_item("appointment", [visit.task_id], [look.task_id]))
    assert vehicle == "FUSO FI170" and names == ["FUSO FI170L", "FUSO FI170"]
