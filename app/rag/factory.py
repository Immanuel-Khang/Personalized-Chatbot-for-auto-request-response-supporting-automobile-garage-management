"""
[TRACK A - RAG] Chọn retriever theo cấu hình (.env). Đổi cách tìm kiếm = đổi biến môi trường, không sửa code.

RETRIEVER=hybrid|bm25|vector|keyword   (keyword = bản khung cũ, giữ làm mốc so sánh khi đánh giá)
EMBEDDING_PROVIDER=openai|hash|none    (openai không có key -> tự hạ về BM25-only)
RAG_VECTOR_STORE=local                 (sẽ thêm: pgvector)
"""
import logging
from pathlib import Path

from app.config import settings
from app.rag.embeddings import CachedEmbedder, Embedder, HashingEmbedder, OpenAIEmbedder
from app.rag.hybrid import HybridRetriever
from app.rag.retriever import KNOWLEDGE_DIR, KeywordRetriever
from app.rag.vectorstore import LocalVectorStore, VectorStore

logger = logging.getLogger(__name__)


def build_embedder() -> Embedder | None:
    provider = settings.embedding_provider
    inner: Embedder
    if provider == "openai":
        if not settings.openai_api_key:
            logger.warning("EMBEDDING_PROVIDER=openai nhưng thiếu OPENAI_API_KEY -> BM25-only")
            return None
        inner = OpenAIEmbedder(settings.embedding_model, settings.openai_api_key)
    elif provider == "hash":
        inner = HashingEmbedder()
    else:
        return None
    cache = Path(settings.rag_cache_dir) / f"embeddings-{inner.name.replace(':', '_').replace('/', '_')}.json"
    return CachedEmbedder(inner, cache)


def build_vector_store() -> VectorStore:
    if settings.rag_vector_store == "local":
        return LocalVectorStore()
    raise ValueError(f"RAG_VECTOR_STORE={settings.rag_vector_store!r} chưa hỗ trợ (hiện có: local)")


def build_retriever():
    if settings.retriever == "keyword":
        return KeywordRetriever()
    mode = settings.retriever if settings.retriever in ("hybrid", "bm25", "vector") else "hybrid"
    embedder = build_embedder() if mode != "bm25" else None
    return HybridRetriever(
        KNOWLEDGE_DIR, embedder, build_vector_store() if embedder else None, mode=mode,
        candidates=settings.rag_candidates, rrf_k=settings.rag_rrf_k,
        weight_bm25=settings.rag_weight_bm25, weight_vector=settings.rag_weight_vector,
        min_keyword_hits=settings.rag_min_keyword_hits, min_vector_sim=settings.rag_min_vector_sim,
    )
