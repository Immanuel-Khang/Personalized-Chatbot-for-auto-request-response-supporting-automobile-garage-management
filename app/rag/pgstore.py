"""
[TRACK A - RAG] Chỉ mục RAG trên PostgreSQL: pgvector (nhánh vector) + full-text search (nhánh từ khóa, tùy chọn).

Bảng `rag_chunks` (xem app/db/models.py) lưu chunk + vector + tsvector, khóa theo (namespace, id) với id = hash nội dung.
- sync(chunks, embedder): upsert chunk, xóa chunk không còn trong file, chỉ embed chunk MỚI hoặc đổi model.
  Vector nằm trong DB nên không cần cache embedding ra file, và mọi worker/tiến trình dùng chung một chỉ mục.
- search(vector, n): cosine qua HNSW (toán tử <=>), chỉ xét vector cùng model đã embed.
- PgFtsIndex: nhánh từ khóa bằng tsvector ('simple' trên token đã bỏ dấu của app/rag/text.py).

File data/knowledge/ vẫn là nguồn sự thật; DB chỉ là chỉ mục có thể dựng lại bất cứ lúc nào (python -m app.rag.cli reindex).
Cột embedding cố định RAG_EMBEDDING_DIM chiều (migration). Model khác chiều -> sync báo lỗi -> retriever hạ về BM25-only.
"""
import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.rag.chunking import Chunk
from app.rag.text import content_words, tokenize

logger = logging.getLogger(__name__)

NAMESPACE = "knowledge"
_SAFE_TERM = re.compile(r"^[a-z0-9]+$")


def _lit(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def _lex_text(c: Chunk) -> str:
    return " ".join(tokenize(c.index_text, bigrams=False))


class PgVectorStore:
    def __init__(self, engine: Engine, namespace: str = NAMESPACE, dim: int | None = None) -> None:
        from app.db.models import RAG_EMBEDDING_DIM
        self.engine, self.ns, self.dim = engine, namespace, dim or RAG_EMBEDDING_DIM
        self.model: str | None = None  # model của vector đã đồng bộ; search chỉ xét vector cùng model

    # ---- đồng bộ ------------------------------------------------------
    def sync(self, chunks: list[Chunk], embedder=None) -> dict:
        """Đưa DB khớp với `chunks`. Trả {chunks, embedded, deleted}. Embed chunk mới/đổi model (ngoài transaction)."""
        ids = [c.id for c in chunks]
        model = getattr(embedder, "name", None) if embedder else None
        with self.engine.connect() as conn:
            existing = dict(conn.execute(
                text("SELECT id, embedding_model FROM rag_chunks WHERE namespace=:ns"), {"ns": self.ns}).all())
            has_vec = {r[0] for r in conn.execute(
                text("SELECT id FROM rag_chunks WHERE namespace=:ns AND embedding IS NOT NULL"), {"ns": self.ns})}
        need = [c for c in chunks if embedder and not (c.id in has_vec and existing.get(c.id) == model)]
        vecs = embedder.embed_documents([c.index_text for c in need]) if need else []
        for v in vecs:
            if len(v) != self.dim:
                raise ValueError(f"embedding {len(v)} chiều, cột rag_chunks.embedding là {self.dim} chiều (cần migration)")
        rows = [{"ns": self.ns, "id": c.id, "source": c.source, "section": c.section, "kind": c.kind,
                 "tags": list(c.tags), "text": c.text, "index_text": c.index_text, "lex": _lex_text(c)}
                for c in chunks]
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"rag_sync:{self.ns}"})
            if rows:
                conn.execute(text("""
                    INSERT INTO rag_chunks (namespace, id, source, section, kind, tags, text, index_text, lex_text)
                    VALUES (:ns, :id, :source, :section, :kind, :tags, :text, :index_text, :lex)
                    ON CONFLICT (namespace, id) DO UPDATE SET source=EXCLUDED.source, section=EXCLUDED.section,
                      kind=EXCLUDED.kind, tags=EXCLUDED.tags, text=EXCLUDED.text, index_text=EXCLUDED.index_text,
                      lex_text=EXCLUDED.lex_text, updated_at=now()"""), rows)
            if need:
                conn.execute(text("UPDATE rag_chunks SET embedding=CAST(:v AS vector), embedding_model=:m, updated_at=now() "
                                  "WHERE namespace=:ns AND id=:id"),
                             [{"v": _lit(v), "m": model, "ns": self.ns, "id": c.id} for c, v in zip(need, vecs)])
            deleted = conn.execute(text("DELETE FROM rag_chunks WHERE namespace=:ns AND NOT (id = ANY(:ids))"),
                                   {"ns": self.ns, "ids": ids}).rowcount
        self.model = model
        logger.info("pgvector sync: %d chunk, embed mới %d, xóa %d", len(chunks), len(need), deleted)
        return {"chunks": len(chunks), "embedded": len(need), "deleted": deleted}

    # ---- giao diện VectorStore (app/rag/vectorstore.py) ----------------
    def replace_all(self, ids: list[str], vectors: list[list[float]]) -> None:
        """Ghi vector cho chunk ĐÃ có trong bảng (sync() là đường chính; hàm này để giữ đúng Protocol)."""
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE rag_chunks SET embedding=CAST(:v AS vector), embedding_model=:m "
                              "WHERE namespace=:ns AND id=:id"),
                         [{"v": _lit(v), "m": self.model or "unknown", "ns": self.ns, "id": i} for i, v in zip(ids, vectors)])

    def search(self, vector: list[float], n: int) -> list[tuple[str, float]]:
        q = _lit(vector)
        sql = ("SELECT id, 1 - (embedding <=> CAST(:q AS vector)) FROM rag_chunks "
               "WHERE namespace=:ns AND embedding IS NOT NULL" + (" AND embedding_model=:m" if self.model else "") +
               " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n")
        with self.engine.connect() as conn:
            return [(r[0], float(r[1])) for r in conn.execute(
                text(sql), {"q": q, "ns": self.ns, "n": n, **({"m": self.model} if self.model else {})})]

    def count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM rag_chunks WHERE namespace=:ns"), {"ns": self.ns}).scalar_one()


class PgFtsIndex:
    """Nhánh từ khóa bằng full-text của Postgres, cùng giao diện search() với BM25Index.

    Trả [(vị trí chunk, điểm ts_rank, số từ khóa của câu hỏi có trong chunk)]. Số từ khóa khớp tính ở Python
    (cùng hàm content_words) nên ngưỡng RAG_MIN_KEYWORD_HITS giữ nguyên nghĩa. Chưa có bigram như BM25 Python.
    """

    def __init__(self, engine: Engine, chunks: list[Chunk], namespace: str = NAMESPACE) -> None:
        self.engine, self.ns = engine, namespace
        self._pos = {c.id: i for i, c in enumerate(chunks)}
        self._words = [set(tokenize(c.index_text, bigrams=False)) for c in chunks]

    def search(self, query: str, n: int) -> list[tuple[int, float, int]]:
        q_words = content_words(query)
        terms = sorted(w for w in q_words if _SAFE_TERM.match(w))
        if not terms:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, ts_rank_cd(tsv, q, 1) FROM rag_chunks, to_tsquery('simple', :q) q "
                "WHERE namespace=:ns AND tsv @@ q ORDER BY 2 DESC LIMIT :n"),
                {"q": " | ".join(terms), "ns": self.ns, "n": n}).all()
        return [(self._pos[r[0]], float(r[1]), len(q_words & self._words[self._pos[r[0]]]))
                for r in rows if r[0] in self._pos]
