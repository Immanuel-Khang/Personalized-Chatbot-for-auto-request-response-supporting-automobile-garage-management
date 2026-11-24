"""Đợt 3 / L5: benchmark hội thoại với LLM thật (benchmark/l5_cases.json, 50 ca: 40 ca gốc + 10 ca dependency, nhóm 'dependency'). KHÔNG chạy mặc định.

  RUN_LLM_TESTS=1 pytest tests/test_l5_benchmark.py -m llm -q                 # chạy hết (repeat=1)
  RUN_LLM_TESTS=1 L5_GROUPS=policy,booking pytest tests/test_l5_benchmark.py -m llm
  RUN_LLM_TESTS=1 pytest tests/test_l5_benchmark.py -m llm -k "BK_FULL_CONFIRM or PL_DISCOUNT"
  L5_REPEAT=3 ...                                                              # lặp mỗi ca 3 lần (tốn 3x)
Biến: L5_BUDGET_USD (trần chi, mặc định 1.50), L5_REASONING_EFFORT (mặc định low). Báo cáo: reports/l5/l5_<giờ>.{json,md}.
Chấm bằng code (trace, regex, DB, giá theo catalog, tool-call); LLM Judge và chấm tay làm sau.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

import pytest

from app.agent.nodes.output_guard import FORBIDDEN
from app.agent.nodes.router import WRITERS
from app.db.models import Appointment, AppointmentSlot, HandoverTicket, Message
from app.db.seed import seed_catalog
from app.db.session import SessionLocal
from tests import l5, llm_budget
from tests.conversation_helpers import supported_amounts, unique_phone

pytestmark = pytest.mark.llm

CASES = json.loads((Path(__file__).resolve().parents[1] / "benchmark" / "l5_cases.json").read_text(encoding="utf-8"))
REPEAT = max(1, int(os.getenv("L5_REPEAT", "1")))
GROUPS = {g.strip() for g in os.getenv("L5_GROUPS", "").split(",") if g.strip()}
ACTIVE = ("PENDING_APPROVAL", "CONFIRMED")
PARAMS = [pytest.param(case, run, id=f"{case['id']}#{run}") for case in CASES
          for run in range(REPEAT) if not GROUPS or case["group"] in GROUPS]


@pytest.fixture(scope="module", autouse=True)
def catalog():
    seed_catalog()


@pytest.fixture(autouse=True)
def clean_db():
    with SessionLocal() as db:
        for model in (Appointment, AppointmentSlot, HandoverTicket, Message):
            db.query(model).delete()
        db.commit()


def _db_counts(session_id: str) -> tuple[int, int, int]:
    with SessionLocal() as db:
        rows = db.query(Appointment).filter_by(session_id=session_id).all()
        tickets = len(db.query(HandoverTicket).filter_by(session_id=session_id).all())
    return sum(r.status in ACTIVE for r in rows), sum(r.status == "CANCELLED" for r in rows), tickets


@pytest.mark.parametrize("case,run", PARAMS)
def test_l5_case(client, tool_call_spy, case, run):
    result = l5.CaseResult(case["id"], case["group"], case.get("priority", "normal"), run,
                           needs_tool=bool(case.get("needs_tool")))
    session_id, phone, before = f"l5-{case['id']}-{uuid.uuid4().hex[:8]}", unique_phone(), llm_budget.METER.snapshot()
    last_trace: list[str] = []
    known = {int(a) for a in supported_amounts() if a.isdigit()}   # giá catalog, chi phí mốc bảo dưỡng, số trong tài liệu
    echoed: set[int] = set()                                       # số tiền khách đã nêu: bot nhắc lại là hợp lệ
    tool_names: list[str] = []
    for index, turn in enumerate(case["turns"]):
        spec = turn if isinstance(turn, dict) else {"message": turn}
        message = spec["message"].replace("{phone}", phone)
        echoed |= set(l5.amounts_in(message))
        calls_before = len(tool_call_spy)
        started = time.perf_counter()
        response = client.post("/chat", json={"session_id": session_id, "message": message})
        seconds = time.perf_counter() - started
        assert response.status_code == 200, response.text
        body = response.json()
        reply, trace, mode, last_trace = body["reply"], body["trace"], body["mode"], body["trace"]
        active, cancelled, tickets = _db_counts(session_id)

        step = l5.TurnResult(message, reply, trace, mode, seconds, first=index == 0)
        step.failures += l5.check_turn(spec, reply, trace, mode)
        step.failures += l5.check_db(spec.get("expect_db", {}), active, cancelled, tickets)
        if len([n for n in trace if n in WRITERS]) > 1:
            step.failures.append("hơn một agent ghi dữ liệu trong một lượt")
        if mode == "BOT" and not reply.strip():
            step.failures.append("trả lời rỗng ở chế độ BOT")
        tool_names += l5.dedupe_calls(tool_call_spy[calls_before:])
        unsupported = l5.unsupported_amounts(reply, known, echoed)
        result.price_errors += bool(unsupported)
        result.critical += l5.critical_checks(reply, unsupported, active, FORBIDDEN.search)
        result.turns.append(step)
        result.failures += [f"lượt {index + 1}: {f}" for f in step.failures]

    if "expect_handover" in case and ("human_handover" in last_trace) != case["expect_handover"]:
        result.failures.append(f"expect_handover={case['expect_handover']} nhưng trace cuối: {last_trace}")
    result.tools = tool_names
    result.tool_groups_hit, result.tool_calls_ok = l5.score_tools(case, result.tools)
    if not result.tool_groups_hit:
        result.failures.append(f"thiếu công cụ trong {case['required_tool_groups']}, đã gọi: {result.tools}")
    result.cost_usd = llm_budget.METER.snapshot().minus(before).cost
    l5.RESULTS.append(result)
    assert result.passed, " | ".join(result.critical + result.failures)
