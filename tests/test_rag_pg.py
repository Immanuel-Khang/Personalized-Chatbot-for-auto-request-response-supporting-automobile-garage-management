"""Test RAG trên Postgres (track A): pgvector + full-text, đồng bộ tăng dần. Dùng DB *_test từ conftest, không cần API key."""
import uuid

import pytest
from sqlalchemy import text

from app.db.models import RAG_EMBEDDING_DIM
from app.db.session import engine
from app.rag.chunking import load_chunks
from app.rag.embeddings import HashingEmbedder
from app.rag.hybrid import HybridRetriever
from app.rag.pgstore import PgFtsIndex, PgVectorStore

MD = """# Bảo dưỡng

Thay dầu động cơ định kỳ khoảng mỗi 5.000 km hoặc 6 tháng tùy điều kiện vận hành.

Phanh kêu rít hoặc bàn đạp phanh bị chùng là dấu hiệu cần kiểm tra má phanh sớm.

Áp suất lốp chuẩn của Toyota thường từ 30-35 PSI tùy dòng xe, nên đảo lốp định kỳ.
"""


class Counting(HashingEmbedder):
    def __init__(self, name_suffix=""):
        super().__init__(RAG_EMBEDDING_DIM)
        self.name = f"hash:{RAG_EMBEDDING_DIM}{name_suffix}"
        self.calls: list[int] = []

    def embed_documents(self, texts):
        self.calls.append(len(texts))
        return super().embed_documents(texts)


@pytest.fixture(scope="module", autouse=True)
def _migrated():
    """conftest tạo schema rỗng; đưa DB test lên migration mới nhất (idempotent) để có bảng rag_chunks."""
    from app.db.session import init_db
    init_db()


@pytest.fixture()
def ns():
    name = f"t_{uuid.uuid4().hex[:8]}"
    yield name
    with engine.begin() as c:
        c.execute(text("DELETE FROM rag_chunks WHERE namespace=:n"), {"n": name})


def _kdir(tmp_path, md=MD):
    (tmp_path / "doc.md").write_text(md, encoding="utf-8")
    return tmp_path


def _rows(ns):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*), count(embedding) FROM rag_chunks WHERE namespace=:n"), {"n": ns}).one()


def test_sync_is_incremental(tmp_path, ns):
    store, emb = PgVectorStore(engine, ns), Counting()
    chunks = load_chunks(_kdir(tmp_path))
    assert store.sync(chunks, emb) == {"chunks": 3, "embedded": 3, "deleted": 0}
    assert _rows(ns) == (3, 3)
    assert store.sync(chunks, emb)["embedded"] == 0 and emb.calls == [3]  # lần 2: không embed lại
    chunks2 = load_chunks(_kdir(tmp_path, MD + "\n# Bugi\n\nBugi nên thay sau mỗi 40.000 km với bugi thường, bugi iridium bền hơn nhiều.\n"))
    assert store.sync(chunks2, emb) == {"chunks": 4, "embedded": 1, "deleted": 0}
    chunks3 = chunks2[:2]  # bỏ bớt đoạn -> bị xóa khỏi DB
    assert store.sync(chunks3, emb)["deleted"] == 2 and _rows(ns) == (2, 2)


def test_other_model_triggers_reembed_and_search_filters_model(tmp_path, ns):
    store, chunks = PgVectorStore(engine, ns), load_chunks(_kdir(tmp_path))
    a, b = Counting("-a"), Counting("-b")
    store.sync(chunks, a)
    assert store.sync(chunks, b)["embedded"] == 3  # đổi model -> embed lại hết
    assert store.model == b.name
    assert store.search(b.embed_query("phanh kêu"), 3)


def test_vector_search_ranks_by_cosine(tmp_path, ns):
    store, emb = PgVectorStore(engine, ns), Counting()
    chunks = load_chunks(_kdir(tmp_path))
    store.sync(chunks, emb)
    hits = store.search(emb.embed_query("phanh kêu rít má phanh"), 3)
    top = next(c for c in chunks if c.id == hits[0][0])
    assert "phanh" in top.text.lower() and hits[0][1] >= hits[-1][1] and -1 <= hits[0][1] <= 1.0001


def test_namespaces_are_isolated(tmp_path, ns):
    other = ns + "_x"
    chunks = load_chunks(_kdir(tmp_path))
    PgVectorStore(engine, ns).sync(chunks, Counting())
    PgVectorStore(engine, other).sync(chunks[:1], Counting())
    try:
        assert _rows(ns)[0] == 3 and _rows(other)[0] == 1
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM rag_chunks WHERE namespace=:n"), {"n": other})


def test_dimension_mismatch_raises_and_retriever_falls_back(tmp_path, ns):
    store = PgVectorStore(engine, ns)
    with pytest.raises(ValueError, match="chiều"):
        store.sync(load_chunks(_kdir(tmp_path)), HashingEmbedder(64))
    r = HybridRetriever(tmp_path, HashingEmbedder(64), PgVectorStore(engine, ns))
    assert r.effective_mode == "bm25" and r.retrieve("áp suất lốp", k=1)
    assert _rows(ns)[0] == 3  # chunk vẫn vào DB (không vector) để full-text dùng được


def test_hybrid_end_to_end_on_pgvector(tmp_path, ns):
    r = HybridRetriever(_kdir(tmp_path), Counting(), PgVectorStore(engine, ns))
    assert r.effective_mode == "hybrid"
    res = r.retrieve("phanh kêu thì sao", k=2)
    assert res and "phanh" in res[0].text.lower() and 0 < res[0].score <= 1


def test_reload_picks_up_changes_in_db(tmp_path, ns):
    emb = Counting()
    r = HybridRetriever(_kdir(tmp_path), emb, PgVectorStore(engine, ns))
    assert r.retrieve("bugi iridium", k=1) == []
    (tmp_path / "doc.md").write_text(MD + "\nBugi iridium thay sau mỗi 100.000 km, bugi thường 40.000 km.\n", encoding="utf-8")
    assert r.reload() == 4 and emb.calls == [3, 1]
    assert r.retrieve("bugi iridium", k=1)


def test_fts_branch_matches_and_respects_keyword_threshold(tmp_path, ns):
    chunks = load_chunks(_kdir(tmp_path))
    PgVectorStore(engine, ns).sync(chunks, None)
    fts = PgFtsIndex(engine, chunks, ns)
    pos, score, matched = fts.search("phanh keu", 3)[0]
    assert "phanh" in chunks[pos].text.lower() and score > 0 and matched == 2
    assert fts.search("thời tiết Hà Nội", 3) == [] and fts.search("   ", 3) == []


def test_lexical_pg_retriever(tmp_path, ns):
    r = HybridRetriever(_kdir(tmp_path), None, PgVectorStore(engine, ns), mode="bm25", lexical="pg")
    assert isinstance(r._bm25, PgFtsIndex)
    assert "5.000 km" in r.retrieve("bao lau thi thay dau dong co", k=1)[0].text
    assert r.retrieve("thời tiết hôm nay ở Hà Nội", k=3) == []


def test_factory_falls_back_to_local_when_table_missing(monkeypatch):
    from app.config import Settings
    from app.rag import factory
    from app.rag.vectorstore import LocalVectorStore
    monkeypatch.setattr(factory, "settings", Settings(rag_vector_store="pgvector"))
    monkeypatch.setattr("app.db.session.engine", create_dead_engine())
    assert isinstance(factory.build_vector_store(), LocalVectorStore)


def create_dead_engine():
    from sqlalchemy import create_engine
    return create_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 1})
