"""[TRACK A - RAG] Rank fusion. RRF chỉ dùng thứ hạng nên không cần chuẩn hóa điểm BM25 vs cosine."""


def rrf(rankings: list[list[str]], weights: list[float], k: int = 60) -> dict[str, float]:
    """score(d) = sum_i w_i / (k + rank_i(d)), rank bắt đầu từ 1. Đoạn có mặt ở nhiều danh sách được ưu tiên."""
    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return scores
