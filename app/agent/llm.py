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


def _past(history: list[dict] | None) -> list:
    """history: [{role: summary|user|ai, content}] (agent/memory.py), không chứa tin hiện tại."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    roles = {"user": HumanMessage, "ai": AIMessage}
    return [SystemMessage(content="TÓM TẮT CÁC LƯỢT TRƯỚC:\n" + m["content"]) if m["role"] == "summary"
            else roles.get(m["role"], AIMessage)(content=m["content"]) for m in history or []]


def _messages(system: str, user: str, history: list[dict] | None) -> list:
    """[system, tóm tắt lượt cũ, *cửa sổ tin gần nhất (cũ → mới), tin hiện tại]."""
    from langchain_core.messages import HumanMessage, SystemMessage
    messages = [SystemMessage(content=system), *_past(history), HumanMessage(content=user)]
    _log_llm_call(system, user, history)
    return messages


def _log_llm_call(system: str, user: str, history: list[dict] | None) -> None:
    """Log LLM call details to path_trace for debugging."""
    try:
        import os
        if os.getenv("PATH_TRACE", "on").strip().casefold() in {"0", "off", "false", "no"}:
            return
        from app.agent.path_trace import log_llm_input
        log_llm_input(system, user, history)
    except Exception:
        pass  # path_trace is debug-only; never break the LLM call


def generate(system: str, user: str, history: list[dict] | None = None) -> str | None:
    """Gọi LLM chat completion. Trả None nếu không có LLM."""
    llm = _get_llm()
    if llm is None:
        return None
    try:
        response = llm.invoke(_messages(system, user, history))
        content = response.content
        if isinstance(content, str):
            return content
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    except Exception as e:
        logger.error(f"LLM generate error: {e}")
        return None


def generate_structured(system: str, user: str, output_schema: type[T],
                        history: list[dict] | None = None) -> T | None:
    """Gọi LLM với structured output (Pydantic model). Trả None nếu không có LLM."""
    llm = _get_llm()
    if llm is None:
        print("There is no LLM!!!!")
        return None
    try:
        structured_llm = llm.with_structured_output(output_schema, method="function_calling")
        result = structured_llm.invoke(_messages(system, user, history))
        return cast(T, result)
    except Exception as e:
        logger.error(f"LLM structured output error: {e}")
        return None


def generate_with_tools(system_prompt: str, user_message: str, tools: list[dict],
                       tool_executor: callable, history: list[dict] | None = None,
                       max_turns: int = 5) -> dict:
    """
    Call LLM with tool-calling capability. The LLM can call tools multiple times.

    Args:
        system_prompt: System instructions
        user_message: User's message
        tools: List of tool definitions in OpenAI format
        tool_executor: Function that executes tools: tool_executor(tool_name, tool_input) -> result_dict
        history: Conversation history
        max_turns: Maximum number of LLM turns (to prevent infinite loops)

    Returns:
        {
            "final_response": str,  # The LLM's final response text
            "tool_calls": [{"name": str, "input": dict, "result": dict}, ...],
            "error": str | None
        }
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    llm = _get_llm()
    if llm is None:
        return {"error": "LLM not available", "final_response": None, "tool_calls": []}

    try:
        # Bind tools to LLM
        llm_with_tools = llm.bind_tools(tools)

        # Build initial messages
        messages = [SystemMessage(content=system_prompt), *_past(history), HumanMessage(content=user_message)]

        tool_calls_made = []

        for turn in range(max_turns):
            response = llm_with_tools.invoke(messages)

            # Check if LLM wants to call tools
            if not response.tool_calls:
                # No more tool calls, this is the final response
                content = response.content
                if isinstance(content, str):
                    final_text = content
                else:
                    final_text = "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                                       for part in content)

                return {
                    "final_response": final_text,
                    "tool_calls": tool_calls_made,
                    "error": None
                }

            # Execute each tool call
            messages.append(response)

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_input = tool_call["args"]

                # Execute tool
                tool_result = tool_executor(tool_name, tool_input)

                # Record the tool call
                tool_calls_made.append({
                    "name": tool_name,
                    "input": tool_input,
                    "result": tool_result
                })

                # Add tool result to messages
                import json
                tool_message = ToolMessage(
                    content=json.dumps(tool_result, ensure_ascii=False),
                    tool_call_id=tool_call["id"]
                )
                messages.append(tool_message)

        # Out of tool rounds: one last call with tools disabled, so the model answers from the results it has
        # instead of the turn ending without a reply.
        response = llm.bind_tools(tools, tool_choice="none").invoke(messages)
        content = response.content
        final_text = content if isinstance(content, str) else "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        if final_text.strip():
            return {"final_response": final_text, "tool_calls": tool_calls_made, "error": None}
        return {
            "error": f"Exceeded max_turns ({max_turns})",
            "final_response": None,
            "tool_calls": tool_calls_made
        }

    except Exception as e:
        logger.error(f"LLM generate_with_tools error: {e}")
        return {
            "error": str(e),
            "final_response": None,
            "tool_calls": []
        }
