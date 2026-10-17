"""[TRACK A] KnowledgeService: bọc retriever. Chọn retriever bằng biến RETRIEVER trong .env (app/rag/factory.py)."""
from app.contracts.schemas import KnowledgeChunk
from app.rag.factory import build_retriever


class RagKnowledgeService:
    def __init__(self) -> None:
        self._retriever = build_retriever()

    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]:
        return self._retriever.retrieve(query, k)

    def reload(self) -> int:
        """Đọc lại data/knowledge/ sau khi sửa tài liệu (không cần restart server)."""
        reload = getattr(self._retriever, "reload", None)
        return reload() if reload else 0
