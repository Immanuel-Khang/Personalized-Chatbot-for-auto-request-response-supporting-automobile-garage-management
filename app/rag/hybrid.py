"""
[TRACK A - RAG] HybridRetriever: BM25 (từ khóa) + vector (nghĩa) -> RRF -> (sau này: rerank).

Luồng retrieve(query, k):
  1. BM25 top-N      -> chỉ giữ chunk khớp đủ từ khóa (RAG_MIN_KEYWORD_HITS)
  2. vector top-N    -> chỉ giữ chunk có cosine >= RAG_MIN_VECTOR_SIM
  3. RRF gộp 2 danh sách (trọng số RAG_WEIGHT_BM25 / RAG_WEIGHT_VECTOR)
  4. trả top-k; KHÔNG chunk nào qua ngưỡng -> trả [] -> Knowledge Agent chuyển người

Ngưỡng nằm TRONG retriever (không đổi contract): score trả ra là điểm RRF đã chuẩn hóa về 0..1.
Không có embedder (thiếu key / lỗi API) -> tự hạ về BM25-only, bot vẫn chạy.
Điểm mở rộng về sau: _rerank() (cross-encoder), lọc metadata theo dòng xe, viết lại câu hỏi.
"""
import logging
from pathlib import Path

from app.contracts.schemas import KnowledgeChunk
from app.rag.bm25 import BM25Index
from app.rag.chunking import Chunk, load_chunks
from app.rag.embeddings import Embedder
from app.rag.fusion import rrf
from app.rag.vectorstore import VectorStore

logger = logging.getLogger(__name__)


class HybridRetriever:
    def __init__(
        self,
        knowledge_dir: Path,
        embedder: Embedder | None,
        store: VectorStore | None,
        mode: str = "hybrid",            # hybrid | bm25 | vector
        candidates: int = 10,
        rrf_k: int = 60,
        weight_bm25: float = 1.0,
        weight_vector: float = 1.0,
        min_keyword_hits: int = 2,
        min_vector_sim: float = 0.35,
    ) -> None:
        self.dir, self.embedder, self.store = knowledge_dir, embedder, store
        self.mode, self.candidates, self.rrf_k = mode, candidates, rrf_k
        self.w_bm25, self.w_vec = weight_bm25, weight_vector
        self.min_hits, self.min_sim = min_keyword_hits, min_vector_sim
        self.chunks: list[Chunk] = []
        self._by_id: dict[str, Chunk] = {}
        self._bm25: BM25Index | None = None
        self._vector_ready = False
        self.reload()

    # ---- lập chỉ mục -------------------------------------------------
    def reload(self) -> int:
        """Đọc lại data/knowledge/ và dựng lại chỉ mục (đoạn không đổi dùng lại vector trong cache). Trả số chunk."""
        chunks = load_chunks(self.dir)
        bm25 = BM25Index([c.index_text for c in chunks])
        vector_ready = False
        if self.embedder and self.store and self.mode in ("hybrid", "vector") and chunks:
            try:
                vecs = self.embedder.embed_documents([c.index_text for c in chunks])
                self.store.replace_all([c.id for c in chunks], vecs)
                vector_ready = True
            except Exception as e:
                logger.warning("Không dựng được chỉ mục vector (%s) -> chạy BM25-only", e)
        self.chunks, self._by_id, self._bm25, self._vector_ready = chunks, {c.id: c for c in chunks}, bm25, vector_ready
        logger.info("RAG index: %d chunk, mode=%s, vector=%s", len(chunks), self.mode, vector_ready)
        return len(chunks)

    @property
    def effective_mode(self) -> str:
        if self.mode == "bm25" or not self._vector_ready:
            return "bm25"
        return self.mode

    # ---- truy xuất ---------------------------------------------------
    def _bm25_ranking(self, query: str) -> list[str]:
        assert self._bm25 is not None
        from app.rag.text import content_words
        need = min(self.min_hits, len(content_words(query)))
        if need == 0:
            return []
        hits = self._bm25.search(query, self.candidates)
        return [self.chunks[i].id for i, _score, matched in hits if matched >= need]

    def _vector_ranking(self, query: str) -> list[str] | None:
        """None = nhánh vector không dùng được cho lần này (lỗi API) -> caller hạ về BM25."""
        if not (self._vector_ready and self.embedder and self.store):
            return None
        try:
            hits = self.store.search(self.embedder.embed_query(query), self.candidates)
        except Exception as e:
            logger.warning("Embedding câu hỏi lỗi (%s) -> BM25-only cho lượt này", e)
            return None
        return [cid for cid, sim in hits if sim >= self.min_sim]

    def debug(self, query: str) -> dict:
        """Cho CLI hiệu chỉnh ngưỡng: điểm từng nhánh, từng chunk."""
        out: dict = {"mode": self.effective_mode, "bm25": [], "vector": []}
        if self._bm25:
            for i, s, m in self._bm25.search(query, self.candidates):
                out["bm25"].append((self.chunks[i].label, round(s, 3), m))
        if self._vector_ready and self.embedder and self.store:
            for cid, sim in self.store.search(self.embedder.embed_query(query), self.candidates):
                out["vector"].append((self._by_id[cid].label, round(sim, 3)))
        return out

    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]:
        if not query.strip() or not self.chunks:
            return []
        mode = self.effective_mode
        rankings: list[list[str]] = []
        weights: list[float] = []
        if mode in ("hybrid", "bm25"):
            rankings.append(self._bm25_ranking(query))
            weights.append(self.w_bm25)
        if mode in ("hybrid", "vector"):
            vec = self._vector_ranking(query)
            if vec is None:
                if mode == "vector":  # vector-only mà lỗi -> thử BM25
                    rankings.append(self._bm25_ranking(query))
                    weights.append(self.w_bm25)
            else:
                rankings.append(vec)
                weights.append(self.w_vec)
        fused = rrf(rankings, weights, self.rrf_k)
        if not fused:
            return []
        best_possible = sum(weights) / (self.rrf_k + 1)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]
        return [KnowledgeChunk(text=self._by_id[cid].text, source=self._by_id[cid].label,
                               score=round(s / best_possible, 4)) for cid, s in ranked]
