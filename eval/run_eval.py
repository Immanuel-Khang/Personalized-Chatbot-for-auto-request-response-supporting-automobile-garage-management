"""
[TRACK B] Eval theo TRAJECTORY: kiểm tra agent đi qua đúng các node, không chỉ kiểm tra câu trả lời.
Chạy: python -m eval.run_eval
TODO[EVAL]: thêm baseline RAG-only/rule-based để so sánh; thêm các test case cho 17 yêu cầu.
"""
import json
import uuid
from pathlib import Path

from app.contracts.schemas import ChatRequest
from app.conversation import handle_message
from app.db.seed import seed_catalog
from app.db.session import init_db


def main() -> None:
    init_db()
    seed_catalog()
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        token, conv_id, resp = f"eval-{uuid.uuid4()}", None, None
        for turn in case["turns"]:
            resp = handle_message(ChatRequest(visitor_token=token, message=turn, conversation_id=conv_id))
            conv_id = resp.conversation_id
        path_ok = all(n in resp.trace for n in case["expect_path"])
        handover_ok = ("human_handover" in resp.trace) == case["expect_handover"]
        ok = path_ok and handover_ok
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<26} {' -> '.join(resp.trace)}")
    print(f"\n{passed}/{len(cases)} passed")


if __name__ == "__main__":
    main()
