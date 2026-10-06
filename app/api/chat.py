from fastapi import APIRouter, HTTPException

from app.contracts.schemas import ChatRequest, ChatResponse
from app.conversation import handle_message, messages_since

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # TODO[PLATFORM]: rate limit, idempotency theo message_id, xử lý bất đồng bộ
    return handle_message(req)


@router.get("/chat/{conversation_id}/messages")
def poll_messages(conversation_id: int, visitor_token: str, after: int = 0):
    """Client poll tin nhân viên/hệ thống (vd 'nhân viên đã tiếp nhận'). TODO[PLATFORM]: thay bằng WebSocket/SSE."""
    data = messages_since(conversation_id, visitor_token, after)
    if data is None:
        raise HTTPException(404, "conversation not found")
    return data
