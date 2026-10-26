"""[TRACK B] Eval trajectory từng lượt: tối đa một agent ghi dữ liệu mỗi lượt, agent chỉ đọc chạy song song.
Thứ tự các nhánh song song không cố định nên chỉ kiểm tra theo tập node, không theo thứ tự.
Chạy: LLM_PROVIDER=none python -m eval.run_eval
"""
import json
import uuid
from pathlib import Path

from app.contracts.schemas import ChatRequest
from app.conversation import handle_message
from app.db.seed import seed_catalog
from app.db.session import init_db

WRITERS = {"contract", "appointment"}


def matches(resp, expectation: dict) -> bool:
    return (
        all(n in resp.trace for n in expectation.get("expect_path", []))
        and all(n not in resp.trace for n in expectation.get("forbid_path", []))
        and ("expect_handover" not in expectation or
             ("human_handover" in resp.trace) == expectation["expect_handover"])
        and ("expect_mode" not in expectation or resp.mode.value == expectation["expect_mode"])
        and ("expect_empty_reply" not in expectation or
             (resp.reply == "") == expectation["expect_empty_reply"])
    )


def main() -> int:
    init_db()
    seed_catalog()
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        session_id, resp = f"eval-{uuid.uuid4()}", None
        ok, paths = True, []
        for turn in case["turns"]:
            expectation = turn if isinstance(turn, dict) else {"message": turn}
            resp = handle_message(ChatRequest(session_id=session_id, message=expectation["message"]))
            single_agent = len([n for n in resp.trace if n in WRITERS]) <= 1
            ok = matches(resp, expectation) and single_agent and ok
            paths.append(" -> ".join(resp.trace) or f"({resp.mode.value}: bypass)")
        ok = resp is not None and matches(resp, case) and ok
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<30} {' | '.join(paths)}")
    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
