from fastapi import APIRouter

from app.contracts.schemas import ChatRequest, ChatResponse
from app.conversation import handle_message

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # TODO[PLATFORM]: rate limit, idempotency theo message_id, xử lý bất đồng bộ
    return handle_message(req)
