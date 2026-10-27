"""Lịch sử hội thoại đầy đủ: mỗi lượt LLM nhận các tin user/ai trước đó của đúng phiên (bảng messages), theo thứ tự,
dạng message có role, tin hiện tại đúng một lần. LLM được thay bằng FakeLLM ghi lại danh sách message của từng lần gọi."""
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.llm import _messages
from app.conversation import _history
from app.db.models import CatalogItem, Message
from app.db.session import SessionLocal
from app.identity import get_or_create_session

QUESTION = "Anh/chị có muốn bên em đặt lịch bảo dưỡng không?"


class FakeLLM:
    """Ghi lại đúng danh sách message gửi vào model. Structured output trả None -> preprocess dùng luật."""

    def __init__(self, reply: str = QUESTION):
        self.reply, self.calls = reply, []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self.reply)

    def with_structured_output(self, schema):
        fake = self

        class _Structured:
            def invoke(self, messages):
                fake.calls.append(list(messages))
                return None

        return _Structured()


@pytest.fixture()
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr("app.agent.llm._get_llm", lambda: fake)
    return fake


def new_session() -> str:
    return f"hist-{uuid.uuid4().hex[:12]}"


def say(client, llm: FakeLLM, session_id: str, text: str) -> dict:
    """Một lượt /chat; llm.calls chỉ chứa các lần gọi của lượt này."""
    llm.calls.clear()
    response = client.post("/chat", json={"session_id": session_id, "message": text})
    assert response.status_code == 200
    assert llm.calls, "lượt chat phải gọi LLM ít nhất ở preprocess"
    return response.json()


def stored(session_id: str) -> list[tuple[str, str]]:
    with SessionLocal() as db:
        rows = db.query(Message).filter(Message.session_id == session_id,
                                        Message.role.in_(("user", "ai"))).order_by(Message.id).all()
        return [(m.role, m.content) for m in rows]


def as_messages(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [("HumanMessage" if role == "user" else "AIMessage", content) for role, content in rows]


def assert_turn_context(llm: FakeLLM, expected_history: list[tuple[str, str]], text: str) -> None:
    """Mọi lần gọi LLM trong lượt: [System, *lịch sử đúng thứ tự, Human]; tin hiện tại không lặp trong lịch sử."""
    for call in llm.calls:
        assert isinstance(call[0], SystemMessage) and isinstance(call[-1], HumanMessage)
        assert [(type(m).__name__, m.content) for m in call[1:-1]] == expected_history
        assert not any(m.content == text for m in call[:-1])  # tin hiện tại chỉ nằm ở message cuối
    assert llm.calls[0][-1].content.endswith(text)  # preprocess nhận đúng tin hiện tại


def test_first_turn_persists_and_sends_only_current_message(client, llm):
    sid = new_session()
    body = say(client, llm, sid, "Chào bạn")
    assert_turn_context(llm, [], "Chào bạn")
    assert stored(sid) == [("user", "Chào bạn"), ("ai", body["reply"])]


def test_second_turn_receives_previous_turn(client, llm):
    sid = new_session()
    first = say(client, llm, sid, "Chào bạn")
    say(client, llm, sid, "Xe mình bị kêu phanh")
    assert_turn_context(llm, [("HumanMessage", "Chào bạn"), ("AIMessage", first["reply"])], "Xe mình bị kêu phanh")
    assert [role for role, _ in stored(sid)] == ["user", "ai", "user", "ai"]


def test_short_confirmation_follows_bot_question(client, llm):
    sid = new_session()
    first = say(client, llm, sid, "Chào bạn")
    assert first["reply"] == QUESTION  # câu hỏi do LLM sinh đã được lưu làm tin ai
    say(client, llm, sid, "Có")
    for call in llm.calls:  # "Có" đi ngay sau câu hỏi của bot
        assert (type(call[-2]).__name__, call[-2].content) == ("AIMessage", QUESTION)


def test_ordinal_follow_up_keeps_vehicle_list(client, llm):
    sid = new_session()
    listing = say(client, llm, sid, "Cho mình xem danh sách các mẫu xe")["reply"]
    with SessionLocal() as db:
        names = [n for (n,) in db.query(CatalogItem.name)]
    assert sum(name in listing for name in names) >= 2  # bot đã hiển thị danh sách xe
    say(client, llm, sid, "Mẫu thứ hai thì sao?")
    assert_turn_context(llm, [("HumanMessage", "Cho mình xem danh sách các mẫu xe"), ("AIMessage", listing)],
                        "Mẫu thứ hai thì sao?")


def test_threads_are_isolated(client, llm):
    a, b = new_session(), new_session()
    say(client, llm, a, "Chào bạn, mình là khách A")
    say(client, llm, b, "Xe khách B bị kêu phanh")
    say(client, llm, a, "Có")
    contents = [m.content for call in llm.calls for m in call]
    assert not any("khách B" in c for c in contents)
    assert_turn_context(llm, as_messages(stored(a)[:2]), "Có")


def test_order_and_no_duplicates_over_several_turns(client, llm):
    sid = new_session()
    texts = ["Chào bạn", "Xe mình bị kêu phanh", "Cho mình xem danh sách các mẫu xe", "Có"]
    for text in texts:
        before = stored(sid)
        say(client, llm, sid, text)
        assert_turn_context(llm, as_messages(before), text)
    rows = stored(sid)
    assert [role for role, _ in rows] == ["user", "ai"] * len(texts)  # mỗi lượt đúng 1 tin khách + 1 tin bot
    assert [content for role, content in rows if role == "user"] == texts


def test_history_skips_queued_messages_and_staff(client):
    """Khách gửi dồn: tin user đến sau tin hiện tại chưa được xử lý -> bỏ; tin ai của lượt trước vẫn vào dù id lớn hơn.
    Tin staff/system không đưa vào LLM."""
    sid = new_session()
    with SessionLocal() as db:
        get_or_create_session(db, sid)
        rows = [Message(session_id=sid, role=r, content=c) for r, c in
                [("user", "u1"), ("user", "u2"), ("ai", "a1"), ("system", "s"), ("staff", "nv"), ("user", "u3")]]
        db.add_all(rows)
        db.commit()
        assert _history(db, sid, rows[1].id) == [{"role": "user", "content": "u1"}, {"role": "ai", "content": "a1"}]
        assert _history(db, sid, rows[0].id) == [{"role": "ai", "content": "a1"}]
        assert _history(db, new_session(), rows[0].id) == []


def test_messages_keep_roles_and_order():
    msgs = _messages("sys", "now", [{"role": "user", "content": "u1"}, {"role": "ai", "content": "a1"}])
    assert [(type(m).__name__, m.content) for m in msgs] == [
        ("SystemMessage", "sys"), ("HumanMessage", "u1"), ("AIMessage", "a1"), ("HumanMessage", "now")]
    assert [type(m).__name__ for m in _messages("sys", "now", None)] == ["SystemMessage", "HumanMessage"]
