"""
[TRACK A - RAG] Embedding: giao diện + OpenAI + bản cache + bản băm (offline/test).
Đổi model/nhà cung cấp: chỉ cần viết class có embed_documents / embed_query rồi đăng ký ở factory.py.
Vector cũ KHÔNG dùng lẫn được với model mới -> cache khóa theo (tên model + nội dung).
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

import numpy as np

from app.rag.text import normalize

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(self, model: str, api_key: str, batch_size: int = 64) -> None:
        from langchain_openai import OpenAIEmbeddings
        from pydantic import SecretStr
        self.name = f"openai:{model}"
        self._client = OpenAIEmbeddings(model=model, api_key=SecretStr(api_key))
        self._batch = batch_size

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out.extend(self._client.embed_documents(texts[i:i + self._batch]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


class HashingEmbedder:
    """Embedding băm n-gram ký tự: chạy offline, xác định, KHÔNG hiểu nghĩa. Chỉ để test/dev không cần key."""

    def __init__(self, dim: int = 512) -> None:
        self.name, self.dim = f"hash:{dim}", dim

    def _vec(self, text: str) -> list[float]:
        s = re.sub(r"\s+", " ", normalize(text))
        v = np.zeros(self.dim, dtype=np.float32)
        for n in (3, 4):
            for i in range(len(s) - n + 1):
                h = int(hashlib.md5(s[i:i + n].encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0
        norm = np.linalg.norm(v)
        return (v / norm).tolist() if norm else v.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class CachedEmbedder:
    """Bọc embedder: tài liệu cache ra file (đổi nội dung mới embed lại), câu hỏi cache trong RAM."""

    def __init__(self, inner: Embedder, cache_file: Path | None, query_cache_size: int = 256) -> None:
        self.inner, self.name = inner, inner.name
        self._file = cache_file
        self._docs: dict[str, list[float]] = {}
        self._queries: OrderedDict[str, list[float]] = OrderedDict()
        self._qmax = query_cache_size
        if cache_file and cache_file.exists():
            try:
                self._docs = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception as e:  # cache hỏng -> bỏ, embed lại
                logger.warning("Embedding cache hỏng (%s), bỏ qua", e)

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.name}|{text}".encode("utf-8")).hexdigest()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._docs]
        if missing:
            vecs = self.inner.embed_documents([texts[i] for i in missing])
            for i, v in zip(missing, vecs):
                self._docs[keys[i]] = v
            self._save(set(keys))
        return [self._docs[k] for k in keys]

    def _save(self, keep: set[str]) -> None:
        if not self._file:
            return
        self._docs = {k: v for k, v in self._docs.items() if k in keep}  # dọn vector của đoạn đã bị xóa
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._docs), encoding="utf-8")

    def embed_query(self, text: str) -> list[float]:
        k = self._key(text)
        if k in self._queries:
            self._queries.move_to_end(k)
            return self._queries[k]
        v = self.inner.embed_query(text)
        self._queries[k] = v
        if len(self._queries) > self._qmax:
            self._queries.popitem(last=False)
        return v
