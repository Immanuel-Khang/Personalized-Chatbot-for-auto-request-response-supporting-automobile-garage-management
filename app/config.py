"""Cấu hình tập trung. Mọi thứ đọc từ biến môi trường, không hardcode ở nơi khác."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")  # none | openai
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("OPEN_AI_KEY", ""))
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
    max_react_iterations: int = int(os.getenv("MAX_REACT_ITERATIONS", "3"))
    # --- RAG (xem app/rag/factory.py) ---
    retriever: str = os.getenv("RETRIEVER", "hybrid")  # hybrid | bm25 | vector | keyword
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "openai")  # openai | hash | none
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    rag_vector_store: str = os.getenv("RAG_VECTOR_STORE", "local")
    rag_cache_dir: str = os.getenv("RAG_CACHE_DIR", "./data/.rag_cache")
    rag_candidates: int = int(os.getenv("RAG_CANDIDATES", "10"))
    rag_rrf_k: int = int(os.getenv("RAG_RRF_K", "60"))
    rag_weight_bm25: float = float(os.getenv("RAG_WEIGHT_BM25", "1.0"))
    rag_weight_vector: float = float(os.getenv("RAG_WEIGHT_VECTOR", "1.0"))
    # Ngưỡng "không tìm thấy -> chuyển người": chunk phải khớp >= N từ khóa HOẶC cosine >= ngưỡng.
    rag_min_keyword_hits: int = int(os.getenv("RAG_MIN_KEYWORD_HITS", "2"))
    rag_min_vector_sim: float = float(os.getenv("RAG_MIN_VECTOR_SIM", "0.35"))
    hotline: str = os.getenv("HOTLINE", "0900 000 000")
    handover_timeout_minutes: float = float(os.getenv("HANDOVER_TIMEOUT_MINUTES", "10"))


settings = Settings()
