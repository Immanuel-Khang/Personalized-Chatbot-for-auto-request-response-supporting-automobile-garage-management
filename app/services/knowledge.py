"""[TRACK A] KnowledgeService: bọc retriever. Đổi retriever ở đây khi RAG xịn xong."""
from app.contracts.schemas import KnowledgeChunk
from app.rag.retriever import KeywordRetriever


class RagKnowledgeService:
    def __init__(self) -> None:
        self._retriever = KeywordRetriever()

    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]:
        return self._retriever.retrieve(query, k)
