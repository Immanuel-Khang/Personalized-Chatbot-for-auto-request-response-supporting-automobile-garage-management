"""Đợt 3B: bộ nhớ hội thoại dùng con trỏ theo ID tin nhắn (thay cho đếm số tin). ID: MC01-MC09.

L1: windowed_history với summarized_through_message_id; _history(after_id); _history_cursor (nâng cấp con trỏ cũ).
L3: nhiều lượt qua /chat với cửa sổ nhỏ: summary được tạo, con trỏ tiến lên, mỗi lượt chỉ nạp phần chưa tóm tắt.
"""
import dataclasses
import uuid

import pytest

from app import conversation
from app.agent import memory
from app.config import settings
from app.db.models import Message
from app.db.session import SessionLocal
from app.identity import get_or_create_session
from tests.conversation_helpers import Conversation
from tests.fake_llm import fake_llm, task  # noqa: F401 - fixture


def msgs(n: int, start_id: int = 100) -> list[dict]:
    return [{"id": start_id + i, "role": "user" if i % 2 == 0 else "ai", "content": f"m{i}"} for i in range(n)]


@pytest.fixture()
def fake_summary(monkeypatch):
    calls = []

    def fake(previous, messages):
        calls.append((previous, [m["content"] for m in messages]))
        return f"S{len(calls)}"
    monkeypatch.setattr(memory, "summarize", fake)
    return calls


def small_window(monkeypatch, window: int = 2, batch: int = 2) -> None:
    """Settings là dataclass bất biến: thay cả đối tượng mà memory.py đang dùng."""
    monkeypatch.setattr(memory, "settings", dataclasses.replace(
        settings, history_window_messages=window, history_summary_batch=batch))


# ===== windowed_history =====
def test_mc01_short_history_is_sent_whole_without_a_summary(fake_summary):
    out = memory.windowed_history(msgs(8), None, 0)
    assert fake_summary == [] and out["conversation_summary"] is None
    assert out["summarized_through_message_id"] is None and len(out["history"]) == 8 and out["turn"] == 8


def test_mc02_long_history_is_folded_and_the_cursor_is_the_last_folded_message_id(fake_summary):
    history = msgs(14)                                         # > window(6) + batch(4)
    out = memory.windowed_history(history, None, 0)
    cut = 14 - settings.history_window_messages
    assert fake_summary[0][1] == [m["content"] for m in history[:cut]]
    assert out["summarized_messages"] == cut and out["summarized_through_message_id"] == history[cut - 1]["id"]
    assert out["history"][0] == {"role": "summary", "content": "S1"}
    assert [m["content"] for m in out["history"][1:]] == [m["content"] for m in history[cut:]]


def test_mc03_failed_summary_keeps_the_old_cursor_and_sends_only_the_recent_window(fake_summary, monkeypatch):
    monkeypatch.setattr(memory, "summarize", lambda previous, messages: None)
    out = memory.windowed_history(msgs(14), "cũ", 2, 101)
    limit = settings.history_window_messages + settings.history_summary_batch
    assert out["summarized_through_message_id"] == 101 and out["summarized_messages"] == 2
    assert len(out["history"]) == 1 + limit and out["history"][0]["content"] == "cũ"


def test_mc04_shrunk_history_resets_summary_and_cursor(fake_summary):
    out = memory.windowed_history(msgs(3), "tóm tắt cũ", 9, 555)
    assert out["conversation_summary"] is None and out["summarized_messages"] == 0
    assert out["summarized_through_message_id"] is None


def test_mc05_explicit_turn_overrides_the_slice_length(fake_summary):
    assert memory.windowed_history(msgs(4), None, 0, None, 42)["turn"] == 42
    assert memory.windowed_history(msgs(4), None, 0)["turn"] == 4


# ===== _history / _history_cursor trên DB =====
def new_session_with(rows: list[tuple[str, str]]) -> tuple[str, list[Message]]:
    sid = f"mc-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as db:
        get_or_create_session(db, sid)
        objs = [Message(session_id=sid, role=r, content=c) for r, c in rows]
        db.add_all(objs)
        db.commit()
        for o in objs:
            db.refresh(o)
        return sid, objs


def test_mc06_history_after_id_loads_only_newer_user_and_ai_messages(client):
    sid, rows = new_session_with([("user", "u1"), ("ai", "a1"), ("staff", "nv"), ("user", "u2"), ("ai", "a2"),
                                  ("user", "u3")])
    current = rows[5].id
    with SessionLocal() as db:
        assert [m["content"] for m in conversation._history(db, sid, current)] == ["u1", "a1", "u2", "a2"]
        assert [m["content"] for m in conversation._history(db, sid, current, rows[1].id)] == ["u2", "a2"]
        assert conversation._history(db, sid, current, rows[4].id) == []


def test_mc07_legacy_count_cursor_is_upgraded_to_the_exact_message_id(client):
    sid, rows = new_session_with([("user", "u1"), ("ai", "a1"), ("system", "s"), ("user", "u2"), ("ai", "a2"),
                                  ("user", "u3")])
    with SessionLocal() as db:
        cursor = lambda n: conversation._history_cursor(db, sid, rows[5].id, n)  # noqa: E731
        assert cursor(0) is None
        assert cursor(1) == rows[0].id and cursor(2) == rows[1].id          # tin system không được đếm
        assert cursor(3) == rows[3].id and cursor(4) == rows[4].id
        assert cursor(5) is None                                            # đếm quá số tin thật -> bỏ con trỏ cũ


# ===== /chat nhiều lượt =====
def test_mc08_long_conversation_summarizes_and_only_loads_the_unsummarized_slice(client, fake_llm, monkeypatch):
    small_window(monkeypatch)
    fake_llm.says("summary", "Tóm tắt: khách chào hỏi nhiều lần.")
    loaded: list[tuple[int, int | None]] = []
    real = conversation.run_turn

    def spy(session_id, customer_id, text, negotiation_active=False, mode="BOT", history=None, profile=None,
            conversation_summary=None, summarized_through_message_id=None, turn=None):
        loaded.append((len(history or []), summarized_through_message_id))
        return real(session_id, customer_id, text, negotiation_active, mode, history, profile, conversation_summary,
                    summarized_through_message_id, turn)
    monkeypatch.setattr(conversation, "run_turn", spy)

    conv = Conversation(client, "mc08")
    for i in range(7):
        text = f"Chào bạn lần {i}"
        fake_llm.understands(text, task("GREETING", "greeting", text))
        conv.say(text)

    state = conv.state
    assert state["conversation_summary"] and state["summarized_through_message_id"] is not None
    cursors = [c for _, c in loaded if c is not None]
    assert cursors and cursors == sorted(cursors)                           # con trỏ chỉ tiến, không lùi
    total_prior = [2 * i for i in range(7)]                                  # tin trước mỗi lượt: 0,2,4,...
    assert loaded[-1][0] < total_prior[-1]                                   # lượt cuối KHÔNG nạp lại toàn bộ lịch sử
    assert max(n for n, _ in loaded) <= 2 + 2 + 2                           # window + batch + tin đang chờ


def test_mc09_two_sessions_do_not_share_a_summary_cursor(client, fake_llm, monkeypatch):
    small_window(monkeypatch)
    fake_llm.says("summary", "Tóm tắt riêng.")
    a, b = Conversation(client, "mc09a"), Conversation(client, "mc09b")
    for i in range(6):
        text = f"Xin chào {i}"
        fake_llm.understands(text, task("GREETING", "greeting", text))
        a.say(text)
    text = "Xin chào phiên B"
    fake_llm.understands(text, task("GREETING", "greeting", text))
    b.say(text)
    assert a.state["summarized_through_message_id"] is not None
    assert b.state.get("summarized_through_message_id") is None and not b.state.get("conversation_summary")
