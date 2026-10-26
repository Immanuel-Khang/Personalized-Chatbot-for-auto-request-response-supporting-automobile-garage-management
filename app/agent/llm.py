"""
[TRACK B] Cổng gọi LLM duy nhất. Nodes gọi generate() / generate_structured().
Trả None nghĩa là "không có LLM, dùng fallback rule-based".
"""
import logging
from typing import TypeVar, cast

from pydantic import BaseModel, SecretStr

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# LLM instance (lazy init)
_llm = None
_llm_init_attempted = False


def _get_llm():
    """Lazy-init ChatOpenAI instance."""
    global _llm, _llm_init_attempted
    if _llm_init_attempted:
        return _llm
    _llm_init_attempted = True

    if settings.llm_provider == "none" or not settings.openai_api_key:
        logger.info("LLM_PROVIDER=none hoặc thiếu API key → chạy rule-based")
        return None

    try:
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=settings.openai_temperature,
            api_key=SecretStr(settings.openai_api_key),
        )
        logger.info(f"Đã khởi tạo LLM: {settings.openai_model}")
    except Exception as e:
        logger.warning(f"Không thể khởi tạo LLM: {e}. Fallback rule-based.")
        _llm = None
    return _llm


def generate(system: str, user: str) -> str | None:
    """Gọi LLM chat completion. Trả None nếu không có LLM."""
    llm = _get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return response.content
    except Exception as e:
        logger.error(f"LLM generate error: {e}")
        return None


def generate_structured(system: str, user: str, output_schema: type[T]) -> T | None:
    """Gọi LLM với structured output (Pydantic model). Trả None nếu không có LLM."""
    llm = _get_llm()
    if llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        structured_llm = llm.with_structured_output(output_schema)
        result = structured_llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])
        return cast(T, result)
    except Exception as e:
        logger.error(f"LLM structured output error: {e}")
        return None
