"""
[TRACK A - RAG] Kho vector. Hiện: LocalVectorStore (RAM, numpy) — đủ cho vài nghìn chunk.

Chuyển sang Postgres: viết PgVectorStore có cùng 2 hàm replace_all() / search() rồi đăng ký ở factory.py
(RAG_VECTOR_STORE=pgvector). Sau khi đó có thể bỏ luôn cache embedding file, lưu vector cùng bảng chunk
(cột embedding vector(1536) + cột content_hash để biết đoạn nào cần embed lại), và làm luôn nhánh BM25 bằng
full-text search của Postgres — khi ấy hybrid chạy hoàn toàn bằng SQL.
"""
from typing import Protocol

import numpy as np


class VectorStore(Protocol):
    def replace_all(self, ids: list[str], vectors: list[list[float]]) -> None: ...
    def search(self, vector: list[float], n: int) -> list[tuple[str, float]]: ...  # [(id, cosine)]


class LocalVectorStore:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._m = np.zeros((0, 0), dtype=np.float32)

    def replace_all(self, ids: list[str], vectors: list[list[float]]) -> None:
        m = np.asarray(vectors, dtype=np.float32) if vectors else np.zeros((0, 0), dtype=np.float32)
        if m.size:
            norms = np.linalg.norm(m, axis=1, keepdims=True)
            m = m / np.where(norms == 0, 1, norms)
        self._ids, self._m = list(ids), m  # gán một lần -> an toàn khi đang có request đọc

    def search(self, vector: list[float], n: int) -> list[tuple[str, float]]:
        ids, m = self._ids, self._m
        if not ids:
            return []
        q = np.asarray(vector, dtype=np.float32)
        qn = np.linalg.norm(q)
        sims = m @ (q / qn if qn else q)
        top = np.argsort(-sims)[:n]
        return [(ids[i], float(sims[i])) for i in top]
