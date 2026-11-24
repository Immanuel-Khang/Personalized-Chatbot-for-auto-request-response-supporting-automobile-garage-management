"""Kiểm phần thuần của Đợt 3 (chấm điểm, thống kê, đo tiền) bằng dữ liệu giả: chạy mỗi commit, không gọi LLM."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import l5, llm_budget

CASES = json.loads((Path(__file__).resolve().parents[1] / "benchmark" / "l5_cases.json").read_text(encoding="utf-8"))


def test_wilson_matches_known_values():
    low, high = l5.wilson(8, 10)
    assert (round(low, 3), round(high, 3)) == (0.490, 0.943)
    assert l5.wilson(0, 0) == (0.0, 0.0) and l5.wilson(10, 10)[1] == 1.0


def test_percentile_interpolates():
    assert l5.percentile([1, 2, 3, 4], 0.5) == 2.5 and l5.percentile([5], 0.95) == 5 and l5.percentile([], 0.5) == 0


def test_check_turn_reports_each_violation():
    spec = {"expect_path": ["sales"], "forbid_path": ["appointment"], "reply_must_match_any": ["5\\.000"],
            "reply_must_not_match": ["tiếp tục chạy"], "max_amount_vnd": 600_000_000, "expect_mode": "BOT"}
    ok = l5.check_turn(spec, "Thay dầu mỗi 5.000 km", ["sales"], "BOT")
    assert ok == []
    bad = l5.check_turn(spec, "Cứ tiếp tục chạy, giá 700.000.000 đồng", ["appointment"], "HUMAN_PENDING")
    assert len(bad) == 6


def test_critical_checks():
    never = lambda text: None
    assert l5.critical_checks("Dạ ok", [], 0, never) == []
    assert len(l5.critical_checks("Đã đặt lịch hẹn cho bạn", [], 0, never)) == 1         # xác nhận đặt khi DB không có
    assert l5.critical_checks("Đã đặt lịch hẹn cho bạn", [], 1, never) == []             # DB có lịch thì hợp lệ
    assert l5.critical_checks("Mình đã ghi nhận bạn muốn đặt lịch bảo dưỡng", [], 0, never) == []
    assert l5.critical_checks("Mình đã ghi nhận lịch bảo dưỡng bạn mong muốn, bạn cho số điện thoại", [], 0, never) == []
    assert "giá" in l5.critical_checks("x", ["999.000.000"], 0, never)[0]
    assert "nội bộ" in l5.critical_checks("price_as_of 2026", [], 0, never)[0]
    assert "giảm giá" in l5.critical_checks("x", [], 0, lambda text: True)[0]


def test_amounts_in_understands_abbreviations_and_suffixes():
    assert l5.amounts_in("Giá 425.000.000 đồng, hoặc 399.000.000đ, 800 triệu, 1,2 tỷ, 600tr, 5 VND") == [
        425_000_000, 399_000_000, 800_000_000, 1_200_000_000, 600_000_000, 5]
    assert l5.amounts_in("Thay dầu mỗi 10.000 km, đặt lịch lúc 10 giờ, còn 3 xe") == []   # không có đơn vị tiền
    assert l5.amounts_in("Mình đặt 10 đặt chỗ") == []                                        # 'đặt' không phải đuôi đ


def test_unsupported_amounts_compares_values_not_spelling():
    known, echoed = {425_000_000, 1_500_000}, {600_000_000}
    assert l5.unsupported_amounts("Giá 425 triệu, bảo dưỡng 1.500.000đ", known, echoed) == []
    assert l5.unsupported_amounts("Ngân sách 600 triệu của bạn", known, echoed) == []         # nhắc lại số khách nêu
    assert l5.unsupported_amounts("Xe này 800 triệu, phụ phí 2.500.000 đồng", known, echoed) == ["800 triệu", "2.500.000 đồng"]


def test_check_turn_budget_cap_sees_abbreviated_amounts():
    spec = {"max_amount_vnd": 600_000_000}
    assert l5.check_turn(spec, "Mẫu này 700 triệu", [], "BOT") != []
    assert l5.check_turn(spec, "Mẫu này 599 triệu", [], "BOT") == []


def test_dedupe_calls_counts_repeated_identical_call_once():
    rec = lambda node, name, **args: SimpleNamespace(node=node, name=name, args=args)
    calls = [rec("sales", "search_products", q="a"), rec("sales", "search_products", q="a"),
             rec("sales", "search_products", q="b"), rec("appointment", "create_appointment")]
    assert l5.dedupe_calls(calls) == ["search_products", "search_products", "create_appointment"]


def test_check_db():
    assert l5.check_db({"appointments": 1, "cancelled": 0, "tickets_min": 1}, 1, 0, 2) == []
    assert len(l5.check_db({"appointments": 1, "cancelled": 1, "tickets_min": 3}, 0, 0, 1)) == 3


def test_score_tools_recall_and_precision():
    case = {"needs_tool": True, "required_tool_groups": [["create_appointment"], ["cancel_appointment"]],
            "ok_tools": ["check_availability"]}
    assert l5.score_tools(case, ["create_appointment", "cancel_appointment", "check_availability"]) == (True, 3)
    assert l5.score_tools(case, ["create_appointment", "search_products"]) == (False, 1)
    assert l5.score_tools({"needs_tool": False}, ["allow_bot", "search_products"]) == (True, 1)


def test_aggregate_and_markdown():
    def result(id_, group, passed, secs, tools=(), needs=False):
        r = l5.CaseResult(id_, group, "normal", 0, needs_tool=needs, tools=list(tools))
        r.turns = [l5.TurnResult("a", "b", [], "BOT", 9.0, True), l5.TurnResult("a", "b", [], "BOT", secs, False)]
        r.failures = [] if passed else ["x"]
        r.tool_groups_hit, r.tool_calls_ok = (True, len(r.tools))
        return r
    results = [result("A", "sales", True, 2.0, ["search_products"], True), result("B", "booking", False, 4.0)]
    agg = l5.aggregate(results)
    assert agg["passed"] == 1 and agg["cases"] == 2 and agg["groups"] == {"booking": (0, 1), "sales": (1, 1)}
    assert agg["latency_p50"] == 3.0 and agg["tool_recall"] == (1, 1)                    # lượt đầu bị loại khỏi độ trễ
    md = l5.render_markdown(results, "gpt-6-luna", "low", "budget")
    assert "Task success | 1/2" in md and "**B**" in md


def test_usage_cost_formula():
    usage = llm_budget.Usage(calls=2, input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000)
    expected = 500_000 * llm_budget.PRICE_IN / 1e6 + 500_000 * llm_budget.PRICE_CACHED / 1e6 + 100_000 * llm_budget.PRICE_OUT / 1e6
    assert usage.cost == pytest.approx(expected)


def test_meter_reads_usage_metadata_from_generations():
    gen = SimpleNamespace(message=SimpleNamespace(usage_metadata={
        "input_tokens": 3000, "output_tokens": 400, "input_token_details": {"cache_read": 1000}}))
    meter = llm_budget.Meter()
    meter.on_llm_end(SimpleNamespace(generations=[[gen], [SimpleNamespace(message=SimpleNamespace(usage_metadata=None))]]))
    assert (meter.total.calls, meter.total.input_tokens, meter.total.cached_tokens, meter.total.output_tokens) == (1, 3000, 1000, 400)
    snap = meter.snapshot()
    meter.on_llm_end(SimpleNamespace(generations=[[gen]]))
    assert meter.total.minus(snap).input_tokens == 3000


def test_benchmark_file_is_well_formed():
    assert len(CASES) == 50 and len({c["id"] for c in CASES}) == 50   # 40 ca gốc + 10 ca dependency (Đợt 3B)
    groups = {}
    for case in CASES:
        groups[case["group"]] = groups.get(case["group"], 0) + 1
        assert case["turns"] and case["priority"] in ("critical", "normal")
        if case.get("needs_tool"):
            assert case["required_tool_groups"]
    known = {"search_products", "find_by_model_name", "search_by_description", "browse_catalog", "find_by_price_range",
             "get_appointment_details", "check_availability", "create_appointment", "reschedule_appointment",
             "cancel_appointment", "handover_to_human", "flag_safety_risk", "lookup_policy", "allow_bot"}
    for case in CASES:   # tên công cụ trong ca phải có thật; req theo bảng truy vết B11 ('-' = không gắn yêu cầu nào)
        assert case["req"] in {"R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "-"}, case["id"]
        assert known >= set(case.get("ok_tools", [])) | {t for g in case.get("required_tool_groups", []) for t in g}, case["id"]
    assert sum(bool(c.get("needs_tool")) for c in CASES) >= 15   # recall cần đủ ca để khoảng tin cậy không quá rộng
    assert groups == {"sales": 8, "knowledge": 8, "booking": 8, "policy": 8, "multi": 4, "oos": 4, "dependency": 10}   # bảng B7 + C4


def test_unknown_capability_accepts_uncertainty_but_rejects_explicit_claim():
    spec = {
        "reply_must_match_any": [r"chưa xác nhận|chưa có thông tin|chưa rõ|cần .* xác nhận"],
        "reply_must_not_match": [
            r"(?:(?:xưởng|gara|chúng tôi|bên mình).{0,20})(?:chắc chắn nhận sửa|hiện đang nhận sửa|đang nhận sửa|có nhận sửa)",
            r"(?:cam kết|đảm bảo)\s+(?:có thể\s+)?sửa\s+hệ thống pin",
        ],
    }
    safe_reply = "Tài liệu hiện chưa xác nhận xưởng có sửa hệ thống pin xe điện hay không, kỹ thuật viên cần kiểm tra và xác nhận trực tiếp."
    unsafe_reply = "Gara bên mình đang nhận sửa hệ thống pin xe điện."
    assert l5.check_turn(spec, safe_reply, [], "BOT") == []
    assert any("regex cấm" in item for item in l5.check_turn(spec, unsafe_reply, [], "BOT"))
