"""FakeLLM cho tầng L3 (Đợt 2): thay LLM thật bằng một đối tượng có kịch bản, KHÔNG gọi mạng.

Cách cắm: app/agent/llm.py lấy mô hình qua `_get_llm()`. Fixture `fake_llm` thay hàm đó bằng FakeLLM, nên MỌI nơi gọi
LLM (preprocess, date_extraction, policy_guard, sales, appointment, compose, knowledge, conversation, contract,
human_handover, memory) đều đi qua đúng đường code thật của llm.py (generate / generate_structured /
generate_with_tools, kể cả vòng lặp gọi công cụ). Chỉ phần "LLM nghĩ gì" được thay bằng kịch bản.

Nguyên tắc: FakeLLM chỉ để kiểm LOGIC TRONG CODE khi LLM đã trả một kết quả cho trước (ghi DB, chặn, chuyển người,
validation...). Nó KHÔNG kiểm bot có hiểu tiếng Việt hay không: việc đó thuộc L5 (LLM thật).

Ví dụ:
    def test_x(client, fake_llm):
        fake_llm.understands(
            "Tôi muốn đặt lịch thay dầu vào ngày mai lúc 10 giờ sáng",
            task("APPOINTMENT", "book_appointment", "đặt lịch thay dầu"),
            slots=[slot("service_type", "MAINTENANCE", "thay dầu"),
                   slot("appointment_when", "ngày mai lúc 10 giờ sáng", "ngày mai lúc 10 giờ sáng")])
        fake_llm.uses_tools("appointment", [[("create_appointment", {})]])
        ...
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from app.agent.date_extraction import DateInfo, Kind, Period, Weekday
from app.contracts.schemas import (ConversationInterpretation, Intent, PolicySignals, SlotOperation, SlotUpdate,
                                   TaskInterpretation, TaskRelationship)

# ---------------------------------------------------------------------------------------------------------------
# Nhận diện "ai đang gọi LLM". Công cụ nhận diện bằng TÊN CÔNG CỤ (hợp đồng ổn định); văn bản thuần nhận diện bằng
# một cụm trong system prompt. test_fake_llm.py::test_text_role_markers_match_real_prompts sẽ đỏ nếu ai đó sửa prompt
# làm lệch cụm này, nên không bao giờ lệch âm thầm.
# ---------------------------------------------------------------------------------------------------------------
TOOL_ROLES: dict[str, set[str]] = {
    "appointment": {"get_appointment_details", "check_availability", "create_appointment", "reschedule_appointment",
                    "cancel_appointment"},
    "policy": {"handover_to_human", "flag_safety_risk", "lookup_policy", "allow_bot"},
    "compose": {"submit_reply"},
    "sales": {"search_by_description", "search_products", "find_by_model_name", "find_by_price_range",
              "browse_catalog"},
}
TEXT_ROLE_MARKERS: dict[str, str] = {
    "knowledge": "chuyên viên kỹ thuật ô tô",
    "conversation": "trợ lý thân thiện của xưởng Foton",
    "handover": "vừa được chuyển (hoặc ghi thêm) cho nhân viên",
    "summary": "tóm tắt hội thoại",
    "contract": "thủ tục hợp đồng mua xe",
}


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


# ---------------------------------------------------------------------------------------------------------------
# Hàm dựng dữ liệu cho kịch bản
# ---------------------------------------------------------------------------------------------------------------
def task(domain: str, action: str, text: str, relationship: str = "NEW_REQUEST", **kw: Any) -> TaskInterpretation:
    """Một task trong ConversationInterpretation. domain: SALES|APPOINTMENT|KNOWLEDGE|CONTRACT|DISCOUNT|..."""
    return TaskInterpretation(task_id=f"task_{uuid.uuid4().hex[:8]}", domain=Intent(domain), action=action,
                              text=text, relationship=TaskRelationship(relationship), **kw)


def slot(key: str, value: Any, evidence: str | None = None, operation: str = "SET") -> SlotUpdate:
    """Một slot_update. evidence phải là đoạn NGUYÊN VĂN trong tin khách (code kiểm), mặc định = value."""
    return SlotUpdate(key=key, operation=SlotOperation(operation),
                      value=value, evidence=str(value) if evidence is None else evidence)


# ---------------------------------------------------------------------------------------------------------------
# Bộ đọc ngày giả (test double cho bước "LLM đọc thành phần ngày"; phép TÍNH ngày vẫn là code thật)
# ---------------------------------------------------------------------------------------------------------------
_WEEKDAY_WORDS = {"2": Weekday.THU_2, "3": Weekday.THU_3, "4": Weekday.THU_4, "5": Weekday.THU_5, "6": Weekday.THU_6,
                  "7": Weekday.THU_7}


def rule_date_info(expression: str) -> DateInfo:
    """Đọc thành phần ngày/giờ bằng vài regex. Chỉ phục vụ test; không phải logic sản phẩm."""
    text = _norm(expression)
    hour = minute = period = None
    if m := re.search(r"(\d{1,2})\s*(?:giờ|h|:)\s*(\d{1,2})?", text):
        hour, minute = int(m.group(1)), int(m.group(2)) if m.group(2) else None
    if re.search(r"\bsáng\b", text):
        period = Period.AM
    elif re.search(r"\b(trưa|chiều|tối|đêm)\b", text):
        period = Period.PM
    clock = {"hour": hour, "minute": minute, "period": period}
    week = 1 if re.search(r"tuần (sau|tới)", text) else 0 if "tuần này" in text else None

    if "hôm nay" in text:
        return DateInfo(kind=Kind.RELATIVE, offset=0, **clock)
    if re.search(r"ngày mai|\bmai\b", text):
        return DateInfo(kind=Kind.RELATIVE, offset=1, **clock)
    if re.search(r"ngày mốt|ngày kia|\bmốt\b", text):
        return DateInfo(kind=Kind.RELATIVE, offset=2, **clock)
    if m := re.search(r"thứ\s*([2-7])\b", text):
        return DateInfo(kind=Kind.WEEKDAY, weekday=_WEEKDAY_WORDS[m.group(1)], week_offset=week, **clock)
    if re.search(r"chủ nhật|\bcn\b", text):
        return DateInfo(kind=Kind.WEEKDAY, weekday=Weekday.CHU_NHAT, week_offset=week, **clock)
    if m := re.search(r"(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{4}))?", text):
        return DateInfo(kind=Kind.MONTH_DAY, day=int(m.group(1)), month=int(m.group(2)),
                        year=int(m.group(3)) if m.group(3) else None, **clock)
    if m := re.search(r"ngày\s*(\d{1,2})\s*tháng\s*(sau|tới|này|\d{1,2})", text):
        month = m.group(2)
        if month.isdigit():
            return DateInfo(kind=Kind.MONTH_DAY, day=int(m.group(1)), month=int(month), **clock)
        return DateInfo(kind=Kind.MONTH_DAY, day=int(m.group(1)), month_offset=0 if month == "này" else 1, **clock)
    return DateInfo(kind=Kind.UNKNOWN, **clock)


# ---------------------------------------------------------------------------------------------------------------
# FakeLLM
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class LLMCall:
    """Một lần FakeLLM được gọi (dùng làm spy: assert node nào gọi công cụ nào)."""
    kind: str            # "structured" | "text" | "tool"
    role: str            # vd "preprocess", "date", "appointment", "knowledge"
    name: str = ""       # tên công cụ (kind="tool")
    args: Any = None


@dataclass
class _ToolScript:
    when: str | None                                    # chuỗi con phải có trong tin nhắn của lượt; None = luôn khớp
    rounds: list[list[tuple[str, Any]]]                 # mỗi round: các (tên công cụ, args | callable(results)->args)
    final: Any                                          # chuỗi | callable(results)->chuỗi


class FakeLLM:
    """Đối tượng giả có đủ giao diện mà app/agent/llm.py dùng: invoke, with_structured_output, bind_tools."""

    def __init__(self, strict: bool = True):
        self.strict = strict
        self.calls: list[LLMCall] = []
        self.unscripted: list[str] = []
        self._interpretations: dict[str, ConversationInterpretation] = {}
        self._date_infos: dict[str, DateInfo] = {}
        self._tool_scripts: dict[str, list[_ToolScript]] = defaultdict(list)
        self._texts: list[tuple[str, str | None, str]] = []

    # ---- khai báo kịch bản ------------------------------------------------------------------------------------
    def understands(self, text: str, *tasks: TaskInterpretation, slots: list[SlotUpdate] | tuple = (),
                    signals: PolicySignals | None = None, **fields: Any) -> None:
        """Khi khách gõ ĐÚNG `text`, preprocess nhận ConversationInterpretation này.
        fields: complexity, accepts_offer, uncertain, presented_cars, message_summary."""
        fields.setdefault("message_summary", text)
        self._interpretations[_norm(text)] = ConversationInterpretation(
            tasks=list(tasks), slot_updates=list(slots), signals=signals or PolicySignals(), **fields)

    def reads_date(self, expression: str, info: DateInfo) -> None:
        """Ép kết quả đọc ngày cho một cụm cụ thể (mặc định dùng rule_date_info)."""
        self._date_infos[_norm(expression)] = info

    def uses_tools(self, role: str, rounds: list[list[tuple[str, Any]]], final: Any = "",
                   when: str | None = None) -> None:
        """role: appointment | policy | sales | compose. Mỗi round là một lượt LLM gọi công cụ; sau round cuối LLM
        trả `final` (chuỗi rỗng = để node dùng câu dự phòng của nó). args có thể là callable(results) -> dict,
        với results là danh sách kết quả công cụ đã chạy (đã parse JSON) để lấy appointment_id v.v.
        Đăng ký sau thắng đăng ký trước; `when` giới hạn theo nội dung tin của lượt."""
        assert role in TOOL_ROLES, f"role phải là một trong {sorted(TOOL_ROLES)}"
        self._tool_scripts[role].append(_ToolScript(when, rounds, final))

    def says(self, role: str, text: str, when: str | None = None) -> None:
        """Văn bản LLM trả cho node dùng generate(): knowledge | conversation | handover | summary | contract."""
        assert role in TEXT_ROLE_MARKERS, f"role phải là một trong {sorted(TEXT_ROLE_MARKERS)}"
        self._texts.append((role, when, text))

    # ---- spy ---------------------------------------------------------------------------------------------------
    def tool_names(self, role: str | None = None) -> list[str]:
        return [c.name for c in self.calls if c.kind == "tool" and (role is None or c.role == role)]

    def assert_clean(self) -> None:
        """Gọi ở cuối test (fixture tự gọi): mọi tin khách đều có kịch bản; không để test 'xanh nhầm' vì bot không hiểu."""
        if self.strict and self.unscripted:
            raise AssertionError("FakeLLM không có kịch bản cho: " + "; ".join(self.unscripted))

    # ---- giao diện LangChain mà llm.py gọi --------------------------------------------------------------------
    def invoke(self, messages: list) -> AIMessage:  # generate()
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        user = _last_human(messages)
        role = role_of_system(str(system))
        self.calls.append(LLMCall("text", role or "?"))
        for r, when, text in reversed(self._texts):
            if r == role and (when is None or _norm(when) in _norm(user)):
                return AIMessage(content=text)
        if role is None:
            self.unscripted.append(f"generate() không nhận diện được vai: {str(system)[:60]!r}")
        return AIMessage(content="")  # "" -> node dùng câu dự phòng xác định của nó

    def with_structured_output(self, schema: type[BaseModel], **_: Any) -> "_StructuredModel":
        return _StructuredModel(self, schema)

    def bind_tools(self, tools: list[dict], tool_choice: str | None = None, **_: Any) -> "_ToolModel":
        return _ToolModel(self, tools, tool_choice)

    # ---- tra kịch bản -----------------------------------------------------------------------------------------
    def _script_for(self, role: str, user: str) -> _ToolScript | None:
        for script in reversed(self._tool_scripts[role]):
            if script.when is None or _norm(script.when) in _norm(user):
                return script
        return None


def role_of_system(system: str) -> str | None:
    folded = _norm(system)
    return next((role for role, marker in TEXT_ROLE_MARKERS.items() if _norm(marker) in folded), None)


def role_of_tools(tools: list[dict]) -> str | None:
    names = {t.get("name") or t.get("function", {}).get("name") for t in tools}
    return next((role for role, known in TOOL_ROLES.items() if names & known), None)


def _last_human(messages: list) -> str:
    return next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")


class _StructuredModel:
    def __init__(self, fake: FakeLLM, schema: type[BaseModel]):
        self.fake, self.schema = fake, schema

    def invoke(self, messages: list):
        fake, user = self.fake, _last_human(messages)
        if self.schema is DateInfo:
            expression = (m.group(1) if (m := re.search(r"Expression:\s*(.*)", user, re.S)) else user).strip()
            fake.calls.append(LLMCall("structured", "date", args=expression))
            return fake._date_infos.get(_norm(expression)) or rule_date_info(expression)
        if self.schema is ConversationInterpretation:
            fake.calls.append(LLMCall("structured", "preprocess", args=user))
            if found := fake._interpretations.get(_norm(user)):
                return found.model_copy(deep=True)
            fake.unscripted.append(f"preprocess: {user!r}")
            return None  # llm.py trả None -> preprocess dùng kết quả trung tính ("chưa hiểu rõ ý")
        raise NotImplementedError(f"FakeLLM chưa hỗ trợ schema {self.schema.__name__}")


class _ToolModel:
    def __init__(self, fake: FakeLLM, tools: list[dict], tool_choice: str | None):
        self.fake, self.tools, self.tool_choice = fake, tools, tool_choice

    def invoke(self, messages: list) -> AIMessage:
        fake = self.fake
        role = role_of_tools(self.tools)
        if role is None:
            fake.unscripted.append("bind_tools: bộ công cụ lạ " + str([t.get("name") for t in self.tools]))
            return AIMessage(content="")
        user_at = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        user, after = _last_human(messages), messages[user_at + 1:]
        results = [json.loads(m.content) for m in after if isinstance(m, ToolMessage)]
        round_index = sum(isinstance(m, AIMessage) for m in after)
        script = fake._script_for(role, user)
        rounds = script.rounds if script else _default_rounds(role, self.tools)
        final = script.final if script else ""
        if self.tool_choice != "none" and round_index < len(rounds):
            allowed = {t.get("name") for t in self.tools}
            calls = []
            for name, args in rounds[round_index]:
                assert name in allowed, f"{role}: công cụ {name!r} không có trong bộ công cụ của node ({sorted(allowed)})"
                args = args(results) if callable(args) else args
                fake.calls.append(LLMCall("tool", role, name, args))
                calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"})
            return AIMessage(content="", tool_calls=calls)
        return AIMessage(content=final(results) if callable(final) else final)


def _default_rounds(role: str, tools: list[dict]) -> list[list[tuple[str, Any]]]:
    """Khi test không khai báo gì: hành vi 'ít bất ngờ nhất' của từng node."""
    names = {t.get("name") for t in tools}
    if role == "appointment":
        return [[("check_availability", {})]] if names == {"check_availability"} else [[("create_appointment", {})]]
    if role == "policy":
        return [[("allow_bot", {})]]
    return []  # sales: không gọi công cụ; compose: không gửi gì -> node dùng bản ghép sẵn


# ---------------------------------------------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def fake_llm(monkeypatch):
    """Thay LLM thật bằng FakeLLM trong suốt test. Cuối test kiểm không có tin khách nào thiếu kịch bản."""
    from app.agent import llm
    fake = FakeLLM()
    monkeypatch.setattr(llm, "_get_llm", lambda: fake)
    yield fake
    fake.assert_clean()


@dataclass
class ToolCallRecord:
    node: str
    name: str
    args: dict
    result: dict


@pytest.fixture()
def tool_call_spy(monkeypatch):
    """Ghi mọi lần công cụ được thực thi, theo node (sales | policy_guard | compose | appointment).
    Bọc generate_with_tools ở 4 nơi import nó; chạy được cả với FakeLLM lẫn LLM thật (L5 dùng để đo precision/recall)."""
    from app.agent import llm
    from app.agent.nodes import appointment, compose, policy_guard, sales
    log: list[ToolCallRecord] = []
    real = llm.generate_with_tools

    def wrap(node: str) -> Callable:
        def wrapper(system_prompt, user_message, tools, tool_executor, history=None, max_turns=5):
            def spy_executor(name, args):
                result = tool_executor(name, args)
                log.append(ToolCallRecord(node, name, dict(args), result))
                return result
            return real(system_prompt, user_message, tools, spy_executor, history=history, max_turns=max_turns)
        return wrapper

    for module in (sales, policy_guard, compose, appointment):
        monkeypatch.setattr(module, "generate_with_tools", wrap(module.__name__.rsplit(".", 1)[1]))
    return log
