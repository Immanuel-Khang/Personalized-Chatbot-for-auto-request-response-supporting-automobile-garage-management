"""
[TRACK A - RAG] Retriever.
Bản khung: tìm theo từ khóa trên các file .md trong data/knowledge (chạy được ngay, không cần API).
TODO[RAG]: thay bằng embedding + vector store (Chroma/pgvector) nhưng GIỮ NGUYÊN hàm retrieve()
(contract: app/contracts/interfaces.py -> KnowledgeService).
"""
import re
from pathlib import Path

from app.contracts.schemas import KnowledgeChunk

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class KeywordRetriever:
    def __init__(self) -> None:
        self.chunks: list[tuple[str, str]] = []  # (text, source)
        for f in sorted(KNOWLEDGE_DIR.glob("*.md")):
            for para in f.read_text(encoding="utf-8").split("\n\n"):
                para = para.strip()
                if para and not para.startswith("#"):
                    self.chunks.append((para, f.name))

    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]:
        q = _tokens(query)
        scored = []
        for text, source in self.chunks:
            overlap = len(q & _tokens(text))
            if overlap >= 2:  # ngưỡng tối thiểu để tránh trả bừa
                scored.append(KnowledgeChunk(text=text, source=source, score=float(overlap)))
        return sorted(scored, key=lambda c: c.score, reverse=True)[:k]
