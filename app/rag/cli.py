"""
Công cụ dòng lệnh cho RAG:
  python -m app.rag.cli reindex            # đọc lại data/knowledge, đồng bộ vào Postgres (chỉ embed đoạn mới/đổi), in số chunk
  python -m app.rag.cli "phanh kêu rít"    # xem điểm từng nhánh (BM25 / vector) và kết quả cuối — dùng để chỉnh ngưỡng
"""
import sys

from app.rag.factory import build_retriever


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    r = build_retriever()
    if sys.argv[1] == "reindex":
        if hasattr(r, "reload"):
            print(f"Đã lập chỉ mục {r.reload()} chunk (mode={getattr(r, 'effective_mode', 'keyword')}, "
                  f"store={type(getattr(r, 'store', None)).__name__}, lexical={getattr(r, 'lexical', '-')})")
        return
    q = " ".join(sys.argv[1:])
    if hasattr(r, "debug"):
        d = r.debug(q)
        print(f"mode={d['mode']}\n-- BM25 (nhãn, điểm, số từ khóa khớp)")
        for row in d["bm25"]:
            print("  ", row)
        print("-- Vector (nhãn, cosine)")
        for row in d["vector"]:
            print("  ", row)
    print("-- Kết quả cuối (sau ngưỡng + RRF)")
    res = r.retrieve(q, 3)
    for c in res:
        print(f"  [{c.score}] {c.source}: {c.text[:90]!r}")
    if not res:
        print("  (rỗng -> bot sẽ chuyển người)")


if __name__ == "__main__":
    main()
