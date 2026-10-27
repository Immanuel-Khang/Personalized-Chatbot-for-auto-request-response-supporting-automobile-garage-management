"""Test RAG (track A): chuẩn hóa, chunking, BM25, RRF, hybrid, cache, ngưỡng chuyển người. Không cần API key."""
import json

from app.rag.bm25 import BM25Index
from app.rag.chunking import load_chunks
from app.rag.embeddings import CachedEmbedder, HashingEmbedder
from app.rag.fusion import rrf
from app.rag.hybrid import HybridRetriever
from app.rag.text import content_words, tokenize
from app.rag.vectorstore import LocalVectorStore

MD = """# Bảo dưỡng

Thay dầu động cơ định kỳ khoảng mỗi 5.000 km hoặc 6 tháng tùy điều kiện vận hành.

Phanh kêu rít hoặc bàn đạp phanh bị chùng là dấu hiệu cần kiểm tra má phanh sớm.

Áp suất lốp chuẩn của Toyota thường từ 30-35 PSI tùy dòng xe, nên đảo lốp định kỳ.
"""


def _dir(tmp_path, md=MD):
    (tmp_path / "doc.md").write_text(md, encoding="utf-8")
    return tmp_path


def _retriever(tmp_path, **kw):
    emb = kw.pop("embedder", HashingEmbedder())
    return HybridRetriever(_dir(tmp_path), emb, LocalVectorStore() if emb else None, **kw)


def test_normalize_accents_numbers_stopwords():
    assert "phanh" in tokenize("Phanh kêu") and "keu" in tokenize("Phanh kêu")
    assert tokenize("phanh keu") == tokenize("Phanh kêu")
    assert "40000" in tokenize("mốc 40.000 km")
    assert "cua" not in tokenize("giá của xe")
    assert content_words("Đèn áp suất dầu") == {"den", "ap", "suat", "dau"}


def test_bm25_ranks_exact_terms_first():
    idx = BM25Index(["thay dầu động cơ", "má phanh kêu rít", "áp suất lốp"])
    assert idx.search("phanh keu", 3)[0][0] == 1


def test_rrf_prefers_docs_in_both_lists():
    s = rrf([["a", "b"], ["c", "a"]], [1.0, 1.0], k=60)
    assert max(s, key=s.get) == "a"


def test_chunking_md_and_faq(tmp_path):
    (tmp_path / "doc.md").write_text(MD, encoding="utf-8")
    (tmp_path / "faq.md").write_text("# FAQ\n\n## Giờ mở cửa?\nXưởng mở 8h-17h.\n", encoding="utf-8")
    (tmp_path / "faq2.json").write_text(json.dumps([{"question": "Có rửa xe không?", "answer": "Có."}]), encoding="utf-8")
    chunks = load_chunks(tmp_path)
    kinds = {(c.source, c.kind) for c in chunks}
    assert ("doc.md", "doc") in kinds and ("faq.md", "faq") in kinds and ("faq2.json", "faq") in kinds
    assert any("Hỏi: Giờ mở cửa?" in c.text for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)


def test_hybrid_finds_relevant_chunk(tmp_path):
    r = _retriever(tmp_path)
    res = r.retrieve("phanh kêu thì sao", k=2)
    assert res and "phanh" in res[0].text.lower()
    assert 0 < res[0].score <= 1


def test_no_match_returns_empty_so_agent_hands_over(tmp_path):
    r = _retriever(tmp_path, embedder=None)  # BM25-only
    assert r.retrieve("thời tiết hôm nay ở Hà Nội", k=3) == []
    assert r.retrieve("   ", k=3) == []


def test_single_keyword_query_still_matches(tmp_path):
    r = _retriever(tmp_path, embedder=None)
    assert r.retrieve("lốp", k=3)  # câu ngắn 1 từ khóa: ngưỡng tự hạ xuống 1


def test_no_embedder_falls_back_to_bm25(tmp_path):
    r = _retriever(tmp_path, embedder=None)
    assert r.effective_mode == "bm25"
    assert r.retrieve("thay dầu động cơ", k=1)


def test_embedding_failure_falls_back_to_bm25(tmp_path):
    class Broken(HashingEmbedder):
        def embed_documents(self, texts):
            raise RuntimeError("api down")
    r = _retriever(tmp_path, embedder=Broken())
    assert r.effective_mode == "bm25" and r.retrieve("áp suất lốp", k=1)


def test_query_embedding_failure_falls_back(tmp_path):
    class QueryBroken(HashingEmbedder):
        def embed_query(self, text):
            raise RuntimeError("api down")
    r = _retriever(tmp_path, embedder=QueryBroken())
    assert r.effective_mode == "hybrid"
    assert r.retrieve("áp suất lốp", k=1)  # vẫn trả nhờ BM25


def test_embedding_cache_only_embeds_new_chunks(tmp_path):
    calls = []

    class Counting(HashingEmbedder):
        def embed_documents(self, texts):
            calls.append(len(texts))
            return super().embed_documents(texts)

    cache = tmp_path / "cache" / "e.json"
    emb = CachedEmbedder(Counting(), cache)
    kd = tmp_path / "kn"
    kd.mkdir()
    r = _retriever(kd, embedder=emb)
    n = len(r.chunks)
    assert calls == [n]
    # thêm một đoạn -> reload chỉ embed đoạn mới
    (kd / "doc.md").write_text(MD + "\nBugi nên thay sau mỗi 40.000 km với bugi thường, bugi iridium thì 100.000 km.\n", encoding="utf-8")
    assert r.reload() == n + 1 and calls == [n, 1]
    # khởi động lại (cache từ file) -> không embed lại gì
    CachedEmbedder(Counting(), cache).embed_documents([c.index_text for c in r.chunks])
    assert calls == [n, 1]


def test_reload_picks_up_new_file(tmp_path):
    r = _retriever(tmp_path, embedder=None)
    assert r.retrieve("rửa xe miễn phí", k=1) == []
    (tmp_path / "faq.md").write_text("## Có rửa xe miễn phí không?\nCó, rửa xe miễn phí sau bảo dưỡng.\n", encoding="utf-8")
    r.reload()
    assert r.retrieve("rửa xe miễn phí", k=1)


def test_suite_rag_does_not_call_embedding_api():
    """Hồi quy: conftest phải tắt embedder thật, nếu không mỗi câu hỏi gọi OpenAI và suite bị treo khi mạng chậm."""
    from app.rag.factory import build_embedder
    assert build_embedder() is None
