"""
Đánh giá RETRIEVAL (khác eval/run_eval.py chấm đường đi của agent).

  python -m eval.run_rag_eval                          # so keyword / bm25 / hybrid (cần key nếu có vector)
  python -m eval.run_rag_eval --configs keyword,bm25   # chọn cấu hình
  python -m eval.run_rag_eval --configs hybrid,hybrid_pg,hybrid_pgfts,bm25_pgfts   # so RAM với Postgres
  python -m eval.run_rag_eval -v                       # in từng câu hỏi trượt
  python -m eval.run_rag_eval --min-recall 0.8         # thoát mã 1 nếu Recall@3 (hybrid) thấp hơn -> dùng cho CI

Chunk "đúng" = chunk chứa TẤT CẢ chuỗi trong gold_contains (so khớp không dấu, không phân biệt hoa thường),
nên bộ câu hỏi không vỡ khi bạn cắt chunk lại hoặc sửa file. Câu "expect_empty" là câu ngoài phạm vi:
retrieve PHẢI trả rỗng (để bot chuyển người thay vì trả lời bừa).

Chỉ số (chỉ tính trên câu TRONG phạm vi, trừ dòng cuối):
  Recall@k      tỷ lệ câu có chunk đúng nằm trong top-k
  MRR@5         trung bình 1/hạng của chunk đúng đầu tiên (0 nếu không có trong top-5)
  Chuyển oan    tỷ lệ câu trong phạm vi mà retrieve rỗng -> bot chuyển người dù có đáp án
  Trả bừa       tỷ lệ câu NGOÀI phạm vi mà retrieve vẫn trả chunk -> bot trả lời khi lẽ ra phải chuyển người
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.rag.factory import build_embedder
from app.rag.hybrid import HybridRetriever
from app.rag.retriever import KNOWLEDGE_DIR, KeywordRetriever
from app.rag.text import normalize
from app.rag.vectorstore import LocalVectorStore
from app.config import settings

CASES = Path(__file__).with_name("rag_cases.json")
KMAX = 5


def build(name: str):
    """Dựng retriever theo tên cấu hình. Trả (retriever, ghi chú) hoặc (None, lý do bỏ qua)."""
    if name == "keyword":
        return KeywordRetriever(), ""
    if name == "bm25":
        return HybridRetriever(KNOWLEDGE_DIR, None, None, mode="bm25",
                               min_keyword_hits=settings.rag_min_keyword_hits), ""
    if name in ("hybrid_pg", "hybrid_pgfts", "bm25_pgfts"):  # Postgres: pgvector (+ full-text thay BM25 RAM)
        from app.db.session import engine
        from app.rag.pgstore import PgVectorStore
        lex = "pg" if name.endswith("fts") else "python"
        emb = None if name == "bm25_pgfts" else build_embedder()
        if emb is None and name != "bm25_pgfts":
            return None, "thiếu OPENAI_API_KEY (hoặc EMBEDDING_PROVIDER=none)"
        r = HybridRetriever(
            KNOWLEDGE_DIR, emb, PgVectorStore(engine), mode="bm25" if emb is None else "hybrid",
            candidates=settings.rag_candidates, rrf_k=settings.rag_rrf_k,
            weight_bm25=settings.rag_weight_bm25, weight_vector=settings.rag_weight_vector,
            min_keyword_hits=settings.rag_min_keyword_hits, min_vector_sim=settings.rag_min_vector_sim, lexical=lex)
        return r, ""
    if name in ("hybrid", "vector"):
        emb = build_embedder()
        if emb is None:
            return None, "thiếu OPENAI_API_KEY (hoặc EMBEDDING_PROVIDER=none)"
        r = HybridRetriever(
            KNOWLEDGE_DIR, emb, LocalVectorStore(), mode=name,
            candidates=settings.rag_candidates, rrf_k=settings.rag_rrf_k,
            weight_bm25=settings.rag_weight_bm25, weight_vector=settings.rag_weight_vector,
            min_keyword_hits=settings.rag_min_keyword_hits, min_vector_sim=settings.rag_min_vector_sim)
        return r, "" if r.effective_mode == name else f"CẢNH BÁO: chạy {r.effective_mode} (embedding lỗi)"
    return None, "tên cấu hình không biết"


def is_relevant(text: str, gold: list[str]) -> bool:
    t = normalize(text)
    return all(normalize(g) in t for g in gold)


def evaluate(retriever, cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        res = retriever.retrieve(c["query"], KMAX)
        if c.get("expect_empty"):
            rows.append({**c, "in_scope": False, "got": len(res), "rank": None})
            continue
        rank = next((i for i, ch in enumerate(res, 1) if is_relevant(ch.text, c["gold_contains"])), None)
        rows.append({**c, "in_scope": True, "got": len(res), "rank": rank,
                     "top": [ch.source for ch in res[:3]]})
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
    ins = [r for r in rows if r["in_scope"]]
    oos = [r for r in rows if not r["in_scope"]]

    def recall(sub, k):
        return sum(1 for r in sub if r["rank"] and r["rank"] <= k) / len(sub) if sub else float("nan")

    def mrr(sub):
        return sum(1 / r["rank"] for r in sub if r["rank"]) / len(sub) if sub else float("nan")

    by_cat = defaultdict(list)
    for r in ins:
        by_cat[r["cat"]].append(r)
    return {
        "n_in": len(ins), "n_oos": len(oos),
        "recall1": recall(ins, 1), "recall3": recall(ins, 3), "recall5": recall(ins, 5), "mrr": mrr(ins),
        "false_handover": sum(1 for r in ins if r["got"] == 0) / len(ins) if ins else float("nan"),
        "false_answer": sum(1 for r in oos if r["got"] > 0) / len(oos) if oos else float("nan"),
        "by_cat": {k: recall(v, 3) for k, v in by_cat.items()},
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="keyword,bm25,hybrid")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--min-recall", type=float, default=None, help="Recall@3 tối thiểu của cấu hình cuối danh sách")
    a = ap.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    results: dict[str, dict] = {}
    for name in [s.strip() for s in a.configs.split(",") if s.strip()]:
        r, note = build(name)
        if r is None:
            print(f"(bỏ qua {name}: {note})")
            continue
        if note:
            print(f"({name}: {note})")
        results[name] = evaluate(r, cases)
    if not results:
        return 1
    n = next(iter(results.values()))
    print(f"\n{n['n_in']} câu trong phạm vi + {n['n_oos']} câu ngoài phạm vi\n")
    names = list(results)
    print(f"{'chỉ số':<26}" + "".join(f"{x:>10}" for x in names))
    for label, key in [("Recall@1", "recall1"), ("Recall@3", "recall3"), ("Recall@5", "recall5"),
                       ("MRR@5", "mrr"), ("Chuyển oan (thấp tốt)", "false_handover"),
                       ("Trả bừa (thấp tốt)", "false_answer")]:
        print(f"{label:<26}" + "".join(f"{results[x][key]:>10.2f}" for x in names))
    print("\nRecall@3 theo nhóm câu hỏi")
    for cat in sorted({c for r in results.values() for c in r["by_cat"]}):
        print(f"{cat:<26}" + "".join(f"{results[x]['by_cat'].get(cat, float('nan')):>10.2f}" for x in names))
    if a.verbose:
        for name, r in results.items():
            print(f"\n== {name}: câu trượt ==")
            for row in r["rows"]:
                if row["in_scope"] and not (row["rank"] and row["rank"] <= 3):
                    print(f"  [{row['id']}] rank={row['rank']} got={row['got']} q={row['query']!r} top={row.get('top')}")
                if not row["in_scope"] and row["got"] > 0:
                    print(f"  [{row['id']}] TRẢ BỪA {row['got']} chunk, q={row['query']!r}")
    if a.min_recall is not None and results[names[-1]]["recall3"] < a.min_recall:
        print(f"\nFAIL: Recall@3 {names[-1]} < {a.min_recall}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
