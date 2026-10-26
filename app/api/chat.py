from fastapi import APIRouter, HTTPException, Path

from app.contracts.schemas import ChatRequest, ChatResponse
from app.conversation import handle_message, messages_since

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # TODO[PLATFORM]: rate limit, idempotency theo message_id, xử lý bất đồng bộ
    return handle_message(req)


@router.get("/chat/{session_id}/messages")
def poll_messages(session_id: str = Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"),
                  after: int = 0):
    """Client poll tin nhân viên/hệ thống (vd 'nhân viên đã tiếp nhận'). TODO[PLATFORM]: thay bằng WebSocket/SSE."""
    data = messages_since(session_id, after)
    if data is None:
        raise HTTPException(404, "session not found")
    return data
