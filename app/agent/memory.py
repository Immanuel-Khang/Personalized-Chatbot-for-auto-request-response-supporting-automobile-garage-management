"""Conversation memory for LLM calls: running summary + recent window.

handle_message reads the checkpoint's summary cursor, then loads only later user/ai messages from
the DB. Once that unsummarized slice exceeds ``history_window_messages + history_summary_batch``,
older messages are folded into the running summary and the last ``history_window_messages`` remain
verbatim. The summary and DB message-ID cursor live in the LangGraph checkpoint. Structured facts
(known_facts, pending_interactions, active_workflow) are checkpointed separately and are not lost
when a message leaves the verbatim window. The summary travels as a ``{"role": "summary"}`` entry
at the front of ``state["history"]``; llm.py sends it as a system message.
"""
import logging

from app.agent.llm import generate
from app.config import settings

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """\
Bạn tóm tắt hội thoại giữa khách và trợ lý gara ô tô để các lượt sau dùng làm ngữ cảnh.
Cập nhật BẢN TÓM TẮT CŨ bằng các TIN NHẮN MỚI; giữ mọi thông tin còn hiệu lực của bản cũ.
Giữ: nhu cầu và tiêu chí của khách (loại xe, tải trọng, ngân sách, mục đích), các mẫu xe bot đã giới thiệu
và khách quan tâm, thông tin khách cung cấp (tên, SĐT, địa chỉ, xe đang dùng, số km), lịch hẹn/hợp đồng đã đề
xuất hoặc đã xác nhận (chỉ ghi "đã đặt" khi bot báo thành công), câu hỏi còn bỏ ngỏ, các yêu cầu đã bị hủy
hoặc thay đổi. Bỏ lời chào, xã giao và nội dung bot trả lời dài dòng.
Viết tiếng Việt, gạch đầu dòng ngắn, tối đa 12 dòng, không bịa thông tin. Chỉ trả về bản tóm tắt."""


def _render(messages: list[dict]) -> str:
    return "\n".join(f"{'Khách' if m['role'] == 'user' else 'Bot'}: {m['content']}" for m in messages)


def summarize(previous: str | None, messages: list[dict]) -> str | None:
    user = f"BẢN TÓM TẮT CŨ:\n{previous or '(chưa có)'}\n\nTIN NHẮN MỚI:\n{_render(messages)}"
    summary = generate(SUMMARY_PROMPT, user)
    return summary.strip() if summary and summary.strip() else None


def windowed_history(history: list[dict], summary: str | None, summarized: int,
                     summarized_through_message_id: int | None = None,
                     turn: int | None = None) -> dict:
    """State update: bounded history for every LLM call, plus the updated summary and fold cursor.

    ``summarized`` counts leading messages in this DB slice already folded into ``summary``.
    The persistent DB cursor is the ID of the last message folded into the summary."""
    window, batch = settings.history_window_messages, settings.history_summary_batch
    if summarized > len(history):  # history shrank (messages deleted): the summary no longer matches
        summary, summarized, summarized_through_message_id = None, 0, None
    if len(history) - summarized > window + batch:
        cut = len(history) - window
        folded = summarize(summary, history[summarized:cut])
        if folded is not None:
            summary, summarized = folded, cut
            summarized_through_message_id = history[cut - 1].get("id", summarized_through_message_id)
        else:
            logger.warning("History summary failed; sending the last %d messages only", window + batch)
    recent = history[max(summarized, len(history) - window - batch):]
    prefix = [{"role": "summary", "content": summary}] if summary else []
    return {"history": prefix + recent, "conversation_summary": summary, "summarized_messages": summarized,
            "summarized_through_message_id": summarized_through_message_id,
            "turn": len(history) if turn is None else turn}
