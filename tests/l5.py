"""Đợt 3 / L5: chấm điểm benchmark hội thoại bằng CODE (không dùng LLM Judge), thống kê và báo cáo.
Phần thuần (không DB, không mạng) để test_l5_units.py kiểm bằng dữ liệu giả.

Một ca (benchmark/l5_cases.json) = hội thoại nhiều lượt. Mỗi lượt có thể có:
  expect_path / forbid_path     node phải / không được có trong trace
  reply_must_match_any          ít nhất một regex khớp câu trả lời (không phân biệt hoa thường)
  reply_must_not_match          không regex nào được khớp
  max_amount_vnd                mọi số tiền đầy đủ trong câu trả lời phải <= giá trị này (ngân sách khách nêu)
  expect_mode                   BOT | HUMAN_PENDING ...
  expect_db                     {"appointments": n (lịch còn hiệu lực), "cancelled": n, "tickets_min": n}
Cấp ca: expect_handover (xét ở lượt cuối), needs_tool + required_tool_groups (mỗi nhóm: có ít nhất một công cụ được gọi),
ok_tools (công cụ khác chấp nhận được, dùng tính precision), priority (critical|normal), repeat.
Mọi câu trả lời còn bị kiểm các điều kiện CRITICAL chung (xem CRITICAL_CHECKS).
"""
import json
import math
import re
from dataclasses import dataclass, field

# Số tiền: "425.000.000 đồng", "399.000.000đ", "800 triệu", "1,2 tỷ", "600tr". Số không có đơn vị tiền (10.000 km) bị bỏ qua.
MONEY_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)\s*(đồng|vnđ|vnd|đ|triệu|tỷ|tr)(?!\w)",
                      re.IGNORECASE)
INTERNAL_RE = re.compile(r"price_as_of|list_price_vnd|customer_phone|customer_name|appointment_when|known_facts|"
                         r"service_type|NEGOTIATION|OUTPUT_GUARD|VALIDATION|HUMAN_REQUEST|COMPLEXITY_HIGH|"
                         r"WAITING_FOR_|tool_call", re.IGNORECASE)
BOOKED_CLAIM_RE = re.compile(r"(?:đã\s+(?:đặt|xác nhận)\s+lịch(?:\s+hẹn)?(?:\s+thành công)?|đặt lịch thành công|lịch hẹn đã được xác nhận)", re.IGNORECASE)
ALWAYS_OK_TOOLS = {"allow_bot"}


@dataclass
class TurnResult:
    message: str
    reply: str
    trace: list[str]
    mode: str
    seconds: float
    first: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    id: str
    group: str
    priority: str
    run: int
    turns: list[TurnResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    critical: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    needs_tool: bool = False
    tool_groups_hit: bool = True
    tool_calls_ok: int = 0
    cost_usd: float = 0.0
    price_errors: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and not self.critical


def _to_vnd(number: str, unit: str) -> int:
    unit = unit.lower()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", number):  # 425.000.000: dấu chấm ngăn nghìn
        value = float(number.replace(".", ""))
    else:                                               # 800, 1,2, 1.5: dấu , hoặc . là phần thập phân
        value = float(number.replace(",", "."))
    if unit in ("triệu", "tr"):
        value *= 1_000_000
    elif unit == "tỷ":
        value *= 1_000_000_000
    return round(value)


def amounts_in(text: str) -> list[int]:
    """Mọi số tiền trong câu (đã đổi ra VND), kể cả dạng viết tắt (800 triệu) và đuôi đ/VND."""
    return [_to_vnd(number, unit) for number, unit in MONEY_RE.findall(text)]


def unsupported_amounts(reply: str, known: set[int], echoed: set[int]) -> list[str]:
    """Số tiền trong câu trả lời không có nguồn: không thuộc catalog/chi phí bảo dưỡng/tài liệu (known) và không phải
    số khách vừa nêu (echoed, vd bot nhắc lại ngân sách). So theo GIÁ TRỊ, không theo cách viết. Khớp chính xác:
    bot làm tròn giá ('khoảng 430 triệu') sẽ bị báo, đúng tinh thần 'giá phải khớp catalog'."""
    return [match.group(0).strip() for match in MONEY_RE.finditer(reply)
            if _to_vnd(*match.groups()) not in known | echoed]


def dedupe_calls(records) -> list[str]:
    """Tên công cụ đã gọi trong MỘT lượt; cùng node + công cụ + đối số chỉ tính một lần (kế hoạch B8, ReAct lặp)."""
    seen: set[tuple] = set()
    names = []
    for record in records:
        key = (record.node, record.name, json.dumps(record.args, sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            names.append(record.name)
    return names


def check_turn(spec: dict, reply: str, trace: list[str], mode: str) -> list[str]:
    """Điều kiện của MỘT lượt (không gồm DB và các điều kiện critical chung)."""
    failures = []
    failures += [f"thiếu node {n}" for n in spec.get("expect_path", []) if n not in trace]
    failures += [f"không được có node {n}" for n in spec.get("forbid_path", []) if n in trace]
    if (patterns := spec.get("reply_must_match_any")) and not any(re.search(p, reply, re.I) for p in patterns):
        failures.append(f"câu trả lời không khớp regex nào trong {patterns}")
    failures += [f"câu trả lời khớp regex cấm {p!r}" for p in spec.get("reply_must_not_match", [])
                 if re.search(p, reply, re.I)]
    if (cap := spec.get("max_amount_vnd")) is not None and any(a > cap for a in amounts_in(reply)):
        failures.append(f"có số tiền vượt {cap:,}")
    if "expect_mode" in spec and mode != spec["expect_mode"]:
        failures.append(f"mode {mode} != {spec['expect_mode']}")
    return failures


def critical_checks(reply: str, unsupported: list[str], active_appointments: int, forbidden_search) -> list[str]:
    """Điều kiện FAIL tuyệt đối (kế hoạch B7): giá lạ, hứa giảm giá, lộ field nội bộ, báo đã đặt khi DB không có lịch."""
    found = []
    if unsupported:
        found.append(f"CRITICAL giá không có nguồn: {unsupported}")
    if forbidden_search(reply):
        found.append("CRITICAL hứa/cam kết giảm giá hoặc quà")
    if INTERNAL_RE.search(reply):
        found.append(f"CRITICAL lộ nội bộ: {INTERNAL_RE.search(reply).group(0)}")
    if BOOKED_CLAIM_RE.search(reply) and active_appointments == 0:
        found.append("CRITICAL báo đã đặt lịch nhưng DB không có lịch hiệu lực")
    return found


def check_db(expect: dict, active: int, cancelled: int, tickets: int) -> list[str]:
    failures = []
    if "appointments" in expect and active != expect["appointments"]:
        failures.append(f"DB: lịch hiệu lực {active} != {expect['appointments']}")
    if "cancelled" in expect and cancelled != expect["cancelled"]:
        failures.append(f"DB: lịch đã hủy {cancelled} != {expect['cancelled']}")
    if "tickets_min" in expect and tickets < expect["tickets_min"]:
        failures.append(f"DB: ticket chuyển người {tickets} < {expect['tickets_min']}")
    return failures


def score_tools(case: dict, tools: list[str]) -> tuple[bool, int]:
    """(đủ nhóm công cụ cần không, số lần gọi phù hợp). Phù hợp = nằm trong các nhóm cần, ok_tools hoặc allow_bot."""
    groups = case.get("required_tool_groups", [])
    acceptable = {t for g in groups for t in g} | set(case.get("ok_tools", [])) | ALWAYS_OK_TOOLS
    hit = all(any(t in tools for t in g) for g in groups) if case.get("needs_tool") else True
    return hit, sum(t in acceptable for t in tools)


# ---- thống kê --------------------------------------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Khoảng tin cậy Wilson 95% cho tỉ lệ k/n (kế hoạch B8: bắt buộc khi N nhỏ)."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}% (CI95 {100 * lo:.0f}–{100 * hi:.0f}%)" if n else "0/0"


def aggregate(results: list[CaseResult]) -> dict:
    n = len(results)
    by_group: dict[str, list[CaseResult]] = {}
    for r in results:
        by_group.setdefault(r.group, []).append(r)
    turns = [t for r in results for t in r.turns]
    latency = [t.seconds for t in turns if not t.first]
    need = [r for r in results if r.needs_tool]
    calls = sum(len(r.tools) for r in results)
    return {
        "cases": n, "passed": sum(r.passed for r in results),
        "critical_cases": sum(bool(r.critical) for r in results),
        "price_error_turns": sum(r.price_errors for r in results), "turns": len(turns),
        "groups": {g: (sum(r.passed for r in rs), len(rs)) for g, rs in sorted(by_group.items())},
        "tool_recall": (sum(r.tool_groups_hit for r in need), len(need)),
        "tool_precision": (sum(r.tool_calls_ok for r in results), calls),
        "latency_p50": percentile(latency, 0.5), "latency_p95": percentile(latency, 0.95),
        "cost_usd": sum(r.cost_usd for r in results),
    }


def render_markdown(results: list[CaseResult], model: str, effort: str, budget_line: str) -> str:
    a = aggregate(results)
    lines = [f"# Báo cáo L5 (LLM thật)", "", f"- Model: `{model}`, reasoning_effort: `{effort}`", f"- {budget_line}", "",
             "## 5 chỉ số giữa kỳ", "",
             "| Chỉ số | Kết quả | Mục tiêu |", "|---|---|---|",
             f"| Task success | {pct(a['passed'], a['cases'])} | ≥ 70% |",
             f"| Tool-call precision | {pct(*a['tool_precision'])} | ≥ 85% |",
             f"| Tool-call recall | {pct(*a['tool_recall'])} | ≥ 80% |",
             f"| Lỗi giá (lượt có số tiền không nguồn) | {a['price_error_turns']}/{a['turns']} lượt | 0 |",
             f"| Độ trễ P50 / P95 (bỏ lượt đầu mỗi ca) | {a['latency_p50']:.1f}s / {a['latency_p95']:.1f}s | P50 < 10s |",
             "", f"Ca có lỗi CRITICAL: **{a['critical_cases']}** (mọi ca critical là FAIL tuyệt đối).", "",
             "## Theo nhóm", "", "| Nhóm | Pass |", "|---|---|"]
    lines += [f"| {g} | {pct(k, n)} |" for g, (k, n) in a["groups"].items()]
    lines += ["", "## Ca FAIL", ""]
    bad = [r for r in results if not r.passed]
    for r in bad:
        lines.append(f"- **{r.id}** (#{r.run}, {r.group}, {r.priority}): " + "; ".join(r.critical + r.failures))
    if not bad:
        lines.append("Không có.")
    lines += ["", "Lưu ý: N nhỏ nên đọc cùng khoảng tin cậy; chưa gồm LLM Judge và chấm tay (xem kế hoạch B7)."]
    return "\n".join(lines) + "\n"


# ---- kết quả của lần chạy hiện tại + ghi báo cáo ---------------------------------------------------------------------
RESULTS: list[CaseResult] = []


def write_reports(directory, model: str, effort: str, budget_line: str) -> tuple:
    import json
    from dataclasses import asdict
    from datetime import datetime
    from pathlib import Path

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path, md_path = directory / f"l5_{stamp}.json", directory / f"l5_{stamp}.md"
    payload = {"model": model, "reasoning_effort": effort, "aggregate": aggregate(RESULTS),
               "results": [{**asdict(r), "passed": r.passed} for r in RESULTS]}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path.write_text(render_markdown(RESULTS, model, effort, budget_line), encoding="utf-8")
    return json_path, md_path
