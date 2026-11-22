"""L1: các hàm thuần còn thiếu test (kế hoạch B3): identity, state, memory, maintenance. Không DB, không LLM."""
from types import SimpleNamespace

import pytest

from app.agent import memory
from app.agent.state import clean_reply, plain_text, strip_greeting
from app.contracts.schemas import ConversationInterpretation, Intent, TaskInterpretation, TaskRelationship
from app.identity import is_full_name, normalize_phone
from app.services.maintenance import COST_MAP, DbMaintenanceService


# ---- identity ----
@pytest.mark.parametrize("raw, expected", [
    ("0912345678", "+84912345678"), ("0912 345 678", "+84912345678"), ("0912.345.678", "+84912345678"),
    ("+84912345678", "+84912345678"), ("84912345678", "+84912345678"),
    ("091234567", None), ("09123456789", None), ("1912345678", None), ("abc", None), ("", None), (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("name, expected", [
    ("Khang", False), ("Dương Định Khang", True), ("Nguyễn An", True), ("  An  ", False), ("", False), (None, False),
    ("0912345678", False),       # một "từ" toàn chữ số không phải tên
    ("12 34", False),            # không từ nào có chữ cái
])
def test_is_full_name(name, expected):
    assert is_full_name(name) is expected


# ---- state: làm sạch câu trả lời ----
@pytest.mark.parametrize("text, expected", [
    ("Chào Khang, mình có thể giúp gì?", "Mình có thể giúp gì?"),
    ("Xin chào bạn! Giá xe là...", "Giá xe là..."),
    ("Giá xe là 425.000.000 đồng", "Giá xe là 425.000.000 đồng"),   # không có lời chào: giữ nguyên
    ("Chào bạn!", "Chào bạn!"),                                      # bỏ lời chào mà hết nội dung thì giữ lại
])
def test_strip_greeting(text, expected):
    assert strip_greeting(text) == expected


def test_plain_text_removes_markdown_and_internal_field_names():
    raw = "## Giá\n* **Foton Aumark**: 425.000.000 đồng (price_as_of: 18/10/2026)\n* `ABS`"
    out = plain_text(raw)
    assert "*" not in out.replace("- ", "") and "#" not in out and "`" not in out
    assert "price_as_of" not in out and "giá áp dụng từ ngày" in out
    assert out.splitlines()[1].startswith("- ")


def _state(greeting: bool) -> dict:
    task = TaskInterpretation(task_id="t", domain=Intent.GREETING if greeting else Intent.SALES, action="x", text="x",
                              relationship=TaskRelationship.NEW_REQUEST)
    return {"conversation_interpretation": ConversationInterpretation(message_summary="x", tasks=[task])}


def test_clean_reply_keeps_greeting_only_when_customer_greeted():
    assert clean_reply(_state(greeting=True), "Chào bạn, mình là trợ lý.") == "Chào bạn, mình là trợ lý."
    assert clean_reply(_state(greeting=False), "Chào bạn, mình là trợ lý.") == "Mình là trợ lý."
    assert clean_reply({}, "**Dạ** vâng") == "Dạ vâng"


# ---- memory.windowed_history: cửa sổ 6, gộp sau ngưỡng 10 ----
def _history(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "ai", "content": f"m{i}"} for i in range(n)]


@pytest.fixture()
def window(monkeypatch):
    monkeypatch.setattr(memory, "settings", SimpleNamespace(history_window_messages=6, history_summary_batch=4))
    calls = []

    def fake_summarize(previous, messages):
        calls.append((previous, [m["content"] for m in messages]))
        return f"TÓM TẮT({len(messages)})"
    monkeypatch.setattr(memory, "summarize", fake_summarize)
    return calls


def test_history_within_threshold_is_sent_whole_without_summary(window):
    out = memory.windowed_history(_history(10), None, 0)  # đúng ngưỡng 6 + 4: chưa gộp
    assert len(out["history"]) == 10 and out["conversation_summary"] is None and out["summarized_messages"] == 0
    assert out["turn"] == 10 and window == []


def test_history_over_threshold_folds_older_messages_into_summary(window):
    out = memory.windowed_history(_history(11), None, 0)
    assert window == [(None, ["m0", "m1", "m2", "m3", "m4"])]          # gộp 5 tin cũ nhất, giữ 6 tin mới
    assert out["summarized_messages"] == 5 and out["conversation_summary"] == "TÓM TẮT(5)"
    assert out["history"][0] == {"role": "summary", "content": "TÓM TẮT(5)"}
    assert [m["content"] for m in out["history"][1:]] == [f"m{i}" for i in range(5, 11)]
    assert out["turn"] == 11  # turn là độ dài lịch sử đầy đủ, không phải cửa sổ


def test_summary_is_incremental_and_cursor_only_moves_forward(window):
    first = memory.windowed_history(_history(11), None, 0)
    second = memory.windowed_history(_history(12), first["conversation_summary"], first["summarized_messages"])
    assert second["summarized_messages"] == 5 and len(window) == 1   # 12 - 5 = 7 <= 10: chưa gộp lần hai
    third = memory.windowed_history(_history(16), "TÓM TẮT(5)", 5)    # 16 - 5 = 11 > 10: gộp tiếp
    assert window[-1][0] == "TÓM TẮT(5)" and window[-1][1][0] == "m5"  # gộp nối tiếp từ con trỏ cũ
    assert third["summarized_messages"] == 10


def test_summary_failure_falls_back_to_a_wider_recent_window(monkeypatch):
    monkeypatch.setattr(memory, "settings", SimpleNamespace(history_window_messages=6, history_summary_batch=4))
    monkeypatch.setattr(memory, "summarize", lambda previous, messages: None)
    out = memory.windowed_history(_history(14), None, 0)
    assert out["conversation_summary"] is None and out["summarized_messages"] == 0
    assert [m["content"] for m in out["history"]] == [f"m{i}" for i in range(4, 14)]  # chỉ 10 tin cuối, không có tóm tắt


def test_history_that_shrank_resets_the_summary(window):
    out = memory.windowed_history(_history(4), "TÓM TẮT CŨ", 9)
    assert out["conversation_summary"] is None and out["summarized_messages"] == 0 and len(out["history"]) == 4


# ---- maintenance: chọn mốc km gần nhất ----
@pytest.mark.parametrize("km, milestone", [
    (1, 5000), (5000, 5000), (7000, 5000), (7501, 10000), (15000, 10000), (16000, 20000),
    (45000, 40000), (100000, 100000), (150000, 100000),
])
def test_nearest_maintenance_milestone(km, milestone):
    got = DbMaintenanceService().get_milestone_details("Foton Aumark", km)
    assert got.km_milestone == milestone and got.estimated_cost == COST_MAP[milestone] and got.tasks


@pytest.mark.parametrize("km", [0, -1, -5000])
def test_invalid_km_gives_none(km):
    assert DbMaintenanceService().get_milestone_details("Foton Aumark", km) is None


def test_equidistant_km_picks_the_lower_milestone():
    # 7500 cách 5000 và 10000 như nhau: hành vi hiện tại chọn mốc thấp hơn (min giữ phần tử đầu danh sách)
    assert DbMaintenanceService().get_milestone_details("x", 7500).km_milestone == 5000
