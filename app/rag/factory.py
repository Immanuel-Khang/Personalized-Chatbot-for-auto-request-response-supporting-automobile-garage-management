"""
[TRACK A - RAG] Chọn retriever theo cấu hình (.env). Đổi cách tìm kiếm = đổi biến môi trường, không sửa code.

RETRIEVER=hybrid|bm25|vector|keyword   (keyword = bản khung cũ, giữ làm mốc so sánh khi đánh giá)
EMBEDDING_PROVIDER=openai|hash|none    (openai không có key -> tự hạ về BM25-only)
RAG_VECTOR_STORE=pgvector|local        (pgvector = lưu chunk+vector trong Postgres; lỗi DB -> tự hạ về local)
RAG_LEXICAL=python|pg                  (nhánh từ khóa: BM25 trong RAM | full-text của Postgres, chỉ có tác dụng với pgvector)
"""
import logging
from pathlib import Path

from app.config import settings
from app.rag.embeddings import CachedEmbedder, Embedder, HashingEmbedder, OpenAIEmbedder
from app.rag.hybrid import HybridRetriever
from app.rag.retriever import KNOWLEDGE_DIR, KeywordRetriever
from app.rag.vectorstore import LocalVectorStore, VectorStore

logger = logging.getLogger(__name__)


def _hash_dim() -> int:
    """Embedding băm phải đúng số chiều cột pgvector, nếu không sync sẽ báo lỗi."""
    if settings.rag_vector_store == "pgvector":
        from app.db.models import RAG_EMBEDDING_DIM
        return RAG_EMBEDDING_DIM
    return 512


def build_embedder() -> Embedder | None:
    provider = settings.embedding_provider
    inner: Embedder
    if provider == "openai":
        if not settings.openai_api_key:
            logger.warning("EMBEDDING_PROVIDER=openai nhưng thiếu OPENAI_API_KEY -> BM25-only")
            return None
        inner = OpenAIEmbedder(settings.embedding_model, settings.openai_api_key)
    elif provider == "hash":
        inner = HashingEmbedder(_hash_dim())
    else:
        return None
    if settings.rag_vector_store == "pgvector":
        return CachedEmbedder(inner, None)  # vector đã bền trong DB -> chỉ cache câu hỏi trong RAM, không ghi file
    cache = Path(settings.rag_cache_dir) / f"embeddings-{inner.name.replace(':', '_').replace('/', '_')}.json"
    return CachedEmbedder(inner, cache)


def build_vector_store() -> VectorStore:
    if settings.rag_vector_store == "pgvector":
        try:
            from sqlalchemy import text

            from app.db.session import engine
            from app.rag.pgstore import PgVectorStore
            with engine.connect() as conn:  # kiểm tra DB sống + đã migrate (bảng rag_chunks, extension vector)
                conn.execute(text("SELECT 1 FROM rag_chunks LIMIT 1"))
            return PgVectorStore(engine)
        except Exception as e:
            logger.warning("RAG_VECTOR_STORE=pgvector nhưng không dùng được (%s) -> dùng local (RAM)", e)
            return LocalVectorStore()
    if settings.rag_vector_store == "local":
        return LocalVectorStore()
    raise ValueError(f"RAG_VECTOR_STORE={settings.rag_vector_store!r} chưa hỗ trợ (hiện có: pgvector, local)")


def build_retriever():
    if settings.retriever == "keyword":
        return KeywordRetriever()
    mode = settings.retriever if settings.retriever in ("hybrid", "bm25", "vector") else "hybrid"
    embedder = build_embedder() if mode != "bm25" else None
    store = build_vector_store() if (embedder or settings.rag_lexical == "pg") else None
    return HybridRetriever(
        KNOWLEDGE_DIR, embedder, store, mode=mode,
        candidates=settings.rag_candidates, rrf_k=settings.rag_rrf_k,
        weight_bm25=settings.rag_weight_bm25, weight_vector=settings.rag_weight_vector,
        min_keyword_hits=settings.rag_min_keyword_hits, min_vector_sim=settings.rag_min_vector_sim,
        lexical=settings.rag_lexical,
    )
