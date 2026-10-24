"""[TRACK B] Hiểu tin nhắn mới theo lượt trước của bot (một luồng diễn giải chung cho LLM và luật):
- Tham chiếu mẫu xe: 'xe thứ 3', 'mẫu đầu tiên', 'nó' → id catalog. Chỉ nhận mẫu bot ĐÃ hiển thị trong lịch sử.
- Trả lời ngắn cho câu hỏi/gợi ý cuối của bot: 'Có' / 'Không' / 'Vậy đặt giúp tôi' → việc bot đã gợi ý (OfferAction).
LLM chỉ đề xuất (FollowUp); code kiểm tra lại với lịch sử + catalog rồi mới thêm task. Mơ hồ → hỏi lại, không đoán.
Không có state riêng: danh sách xe đọc lại từ tin bot trong state['history'] (bảng messages)."""
import re

from app.agent.nodes.router import AFFIRM
from app.agent.state import AgentState
from app.contracts.schemas import BotReply, FollowUp, Intent, OfferAction, Product
from app.services import get_services

ORDINALS = {"đầu tiên": 1, "đầu": 1, "nhất": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6,
            "bảy": 7, "tám": 8, "chín": 9, "mười": 10, "cuối cùng": -1, "cuối": -1}
_ORDINAL_WORDS = "|".join(sorted(ORDINALS, key=len, reverse=True))
# 'xe thứ 3', 'xe 1', 'mẫu thứ hai', 'mẫu đầu tiên'; không khớp 'xe 3 tấn', 'mẫu năm nay'
POSITION_RE = re.compile(rf"\b(?:xe|mẫu|chiếc|dòng)\s+(?:thứ\s+|số\s+)?(\d{{1,2}}|{_ORDINAL_WORDS})\b"
                         r"(?!\s*(?:tấn|triệu|tỷ|chỗ|giờ|km|nay|ngoái|\d))")
PRONOUN_RE = re.compile(r"\bnó\b|\b(?:mẫu|xe|chiếc|cái|dòng)\s+(?:này|đó|ấy|kia)\b")
DECLINE = {"không", "không ạ", "không cần", "không cần đâu", "thôi", "thôi ạ", "chưa", "chưa cần", "để sau",
           "không muốn", "không, cảm ơn", "không cảm ơn", "ko", "k"}
DECLINE_PREFIX_RE = re.compile(r"^(?:không|thôi|chưa)\s*[,.!;]")  # 'Không, tôi muốn xem giá xe khác'
ACCEPT_BOOK_RE = re.compile(r"đặt (?:giúp|luôn|cho|hộ|đi)|book luôn")
WH_RE = re.compile(r"\b(?:nào|gì|bao nhiêu|bao giờ|mấy)\b")  # câu hỏi lấy thông tin, không phải gợi ý có/không
OFFER_ACTIONS = {
    OfferAction.BOOK_APPOINTMENT: re.compile(r"đặt lịch|đặt hẹn|lái thử|mang xe (?:qua|đến|tới)|qua xưởng|ghé xưởng"),
    OfferAction.PRODUCT_DETAIL: re.compile(r"thông tin|chi tiết|thông số|tư vấn (?:kỹ|thêm)"),
    OfferAction.SHOW_CATALOG: re.compile(r"danh sách"),
}
ACTION_LABEL = {OfferAction.BOOK_APPOINTMENT: "đặt lịch", OfferAction.PRODUCT_DETAIL: "gửi thêm thông tin mẫu xe",
                OfferAction.SHOW_CATALOG: "gửi danh sách các mẫu xe"}
PRODUCT_INTENTS = {Intent.OTHER.value, Intent.SALES.value, Intent.DISCOUNT.value}


def last_question(message: str) -> str:
    """Câu hỏi cuối cùng trong tin của bot (câu kết thúc bằng '?')."""
    parts = re.findall(r"[^.!?\n]*\?", message)
    return parts[-1].strip() if parts else ""


def offer_actions(state: AgentState, question: str) -> list[OfferAction]:
    """Việc bot gợi ý trong câu hỏi có/không cuối cùng. Câu hỏi lấy thông tin ('ngày nào?') không phải gợi ý."""
    lowered = question.lower()
    if (state.get("sales_question") or {}).get("asked") == "browse_offer":  # sales đã ghi rõ việc vừa gợi ý
        return [OfferAction.SHOW_CATALOG]
    return [] if WH_RE.search(lowered) else [a for a, rx in OFFER_ACTIONS.items() if rx.search(lowered)]


def products_in(text: str, vehicles: list[Product]) -> list[Product]:
    """Mẫu xe có tên đầy đủ xuất hiện trong text, theo thứ tự xuất hiện (= thứ tự bot hiển thị).
    Tên nằm trong tên dài hơn đã khớp thì bỏ (tránh đếm trùng)."""
    lowered = text.lower()
    spans = [(i, i + len(p.name), p) for p in vehicles if (i := lowered.find(p.name.lower())) >= 0]
    kept = [s for s in spans
            if not any(o is not s and o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0] for o in spans)]
    return [p for _, _, p in sorted(kept, key=lambda s: s[0])]


def resolve_product(text: str, ai_messages: list[str], vehicles: list[Product],
                    llm_name: str | None) -> tuple[Product | None, bool]:
    """(mẫu xe khách nhắc tới, cần hỏi lại). Vị trí tính trên danh sách bot hiển thị gần nhất, không phải truy vấn mới."""
    if products_in(text, vehicles):  # khách nêu thẳng tên mẫu → sales tra theo tên như cũ
        return None, False
    shown = [products_in(m, vehicles) for m in ai_messages]
    by_position = None
    if m := POSITION_RE.search(text.lower()):
        lists = [s for s in shown if len(s) >= 2]
        if shown and len(shown[-1]) >= 2:
            listing = shown[-1]
        elif len({tuple(p.id for p in s) for s in lists}) == 1:
            listing = lists[-1]  # lượt trước xem 1 mẫu, 'mẫu đầu tiên' vẫn chỉ về danh sách duy nhất
        else:
            return None, True  # chưa có danh sách, hoặc nhiều danh sách khác nhau
        word = m.group(1)
        pos = int(word) if word.isdigit() else ORDINALS[word]
        if pos != -1 and not 1 <= pos <= len(listing):
            return None, True
        by_position = listing[pos - 1] if pos > 0 else listing[-1]
    named = None
    if llm_name and len(llm_name.strip()) >= 4:  # LLM phải chép tên bot đã hiển thị; tên không có trong lịch sử bị bỏ
        wanted = llm_name.strip().lower()
        seen = {p.id: p for s in shown for p in s}
        exact = [p for p in seen.values() if p.name.lower() == wanted]
        partial = [p for p in seen.values() if wanted in p.name.lower()]
        named = exact[0] if exact else (partial[0] if len(partial) == 1 else None)
    if by_position and named and named.id != by_position.id:
        return None, True
    if by_position or named:
        return by_position or named, False
    if PRONOUN_RE.search(text.lower()):
        recent = next((s for s in reversed(shown) if s), [])
        return (recent[0], False) if len(recent) == 1 else (None, bool(recent))
    return None, False


def _add_task(result: dict, intent: str, text: str) -> None:
    if not any(t["intent"] == intent for t in result["tasks"]):
        result["tasks"].append({"intent": intent, "text": text})
    result["intents"] = list(dict.fromkeys([i for i in result["intents"] if i != Intent.OTHER.value] + [intent]))


def _reply(text: str, question: str, llm: FollowUp | None) -> str | None:
    if not question:  # bot không hỏi gì → 'Có'/'Không' không phải câu trả lời (kể cả khi LLM nói ACCEPT)
        return None
    if llm and llm.reply_to_bot:
        return llm.reply_to_bot.value
    bare = text.rstrip(".!?, ")
    if bare in AFFIRM or ACCEPT_BOOK_RE.search(text):
        return BotReply.ACCEPT.value
    if bare in DECLINE or DECLINE_PREFIX_RE.search(text):
        return BotReply.DECLINE.value
    return None


def apply_followup(result: dict, state: AgentState, raw: str, llm: FollowUp | None = None) -> None:
    """Ghi result['followup'] đã kiểm tra và thêm task cho router. Không quyết định chính sách:
    giảm giá vẫn qua policy_guard; flow đang dở / việc đang chờ vẫn do router xử lý như cũ."""
    followup: dict = {"source": "llm" if llm else "rules"}
    result["followup"] = followup
    history = state.get("history") or []
    ai = [m["content"] for m in history if m["role"] == "ai"]
    if not ai:
        return
    text = " ".join(raw.lower().split())
    question = last_question(ai[-1]) if history[-1]["role"] == "ai" else ""
    actions = offer_actions(state, question) if question else []
    reply = _reply(text, question, llm)
    followup.update(reply=reply, offer=question or None)
    vehicles: list[Product] | None = None

    def catalog() -> list[Product]:
        nonlocal vehicles
        if vehicles is None:
            vehicles = get_services().product.list_vehicles(limit=1000)
        return vehicles

    # 1) Nhận lời gợi ý: chỉ khi không có flow/việc chờ (router xử lý 'Được' cho chúng) và router không tự gợi ý
    busy = bool(state.get("active_flow") or state.get("pending_intent") or state.get("offer"))
    if reply == BotReply.ACCEPT.value and actions and not busy:
        chosen = llm.accepted_action if llm and llm.accepted_action in actions else None
        if ACCEPT_BOOK_RE.search(text) and OfferAction.BOOK_APPOINTMENT in actions:
            chosen = OfferAction.BOOK_APPOINTMENT
        if chosen is None and len(actions) == 1 and not (llm and llm.needs_clarification):
            chosen = actions[0]
        if chosen is None:
            followup.update(clarify=True,
                            question=f"Dạ, anh/chị muốn em {' hay '.join(ACTION_LABEL[a] for a in actions)} ạ?")
            return
        followup["action"] = chosen.value
        if chosen == OfferAction.BOOK_APPOINTMENT:
            # Câu gợi ý của bot mang loại dịch vụ ('đặt lịch lái thử') → appointment đọc từ task text
            result["tasks"] = [t for t in result["tasks"] if t["intent"] != Intent.APPOINTMENT.value]
            _add_task(result, Intent.APPOINTMENT.value, question)
            return
        if chosen == OfferAction.SHOW_CATALOG:
            result["sales_signals"]["catalog_request"] = True
            _add_task(result, Intent.SALES.value, raw)
            return
        offered = products_in(question, catalog()) or products_in(ai[-1], catalog())
        if len(offered) == 1:
            followup["product_id"] = offered[0].id
        else:
            followup["clarify_product"] = True
        _add_task(result, Intent.SALES.value, raw)
        return

    # 2) Tham chiếu mẫu xe đã hiển thị (cả khi khách xin giảm giá: policy_guard vẫn chuyển người)
    booking = state.get("active_flow") == "appointment" or Intent.APPOINTMENT.value in result["intents"]
    referenced = POSITION_RE.search(text) or PRONOUN_RE.search(text) or (llm and llm.product_name)
    if booking or not referenced or not set(result["intents"]) <= PRODUCT_INTENTS:
        return
    product, clarify = resolve_product(raw, ai, catalog(), llm.product_name if llm else None)
    if product:
        followup["product_id"] = product.id
    elif clarify or (llm and llm.needs_clarification and (POSITION_RE.search(text) or PRONOUN_RE.search(text))):
        followup["clarify_product"] = True
    else:
        return
    _add_task(result, Intent.SALES.value, raw)
