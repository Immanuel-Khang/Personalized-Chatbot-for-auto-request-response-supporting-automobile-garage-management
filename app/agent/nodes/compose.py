"""Response Composer: an LLM agent merges the validated specialist answers into one smooth plain-text reply.

The agent can only finish through the submit_reply tool, and code checks every submission: no markdown,
no new numbers, no discount/commitment wording, no booking/contract claim unless a writer executed this
turn. If no submission passes, the joined answers are sent with markdown stripped. Output Guard still runs after.
"""
import re

from app.agent.llm import generate_with_tools
from app.agent.nodes.output_guard import FORBIDDEN
from app.agent.nodes.validation import CLAIMS
from app.agent.state import AgentState
from app.contracts.schemas import Intent

# Quy tắc văn phong chung cho mọi prompt sinh câu trả lời
STYLE_RULES = """\
- KHÔNG mở đầu bằng lời chào hay gọi tên khách (VD "Chào Khang,") trừ khi khách vừa chào; đi thẳng vào nội dung.
- Nếu không chắc khách muốn gì, hỏi lại ngắn gọn để làm rõ; KHÔNG tự suy đoán.
- Trả lời thẳng câu khách vừa hỏi trước, sau đó mới gợi ý MỘT bước tiếp theo hữu ích (nếu có); không bắt câu nào cũng kết bằng câu hỏi.
- Chỉ hỏi thông tin thật sự cần cho việc đang làm; KHÔNG hỏi lại thông tin khách đã cung cấp.
- Gợi ý một việc (đặt lịch, lái thử, hợp đồng) KHÔNG phải là đã làm; KHÔNG nói đã đặt/xác nhận/áp dụng khi hệ thống chưa báo thành công.
- Chỉ dùng dữ liệu được cung cấp (catalog, tài liệu, kết quả hệ thống); thiếu dữ liệu thì nói rõ phần nào cần nhân viên xác nhận.
- Lịch sự, tự nhiên như nhân viên cố vấn dịch vụ; không lộ tên luồng, intent hay lỗi kỹ thuật nội bộ.
- Nếu các mẫu xe, bảng so sánh hoặc thông số kỹ thuật đã được trình bày ở các lượt trả lời trước đó trong lịch sử trò chuyện, TUYỆT ĐỐI KHÔNG liệt kê hay trình bày lại từ đầu. Đi thẳng vào giải đáp, phân tích điểm khác biệt cho chi tiết mới mà khách vừa nhắn.
"""

COMPOSE_PROMPT = """\
Bạn là Response Composer của xưởng Foton. Bạn nhận các phần trả lời đã được kiểm duyệt (theo thứ tự) và viết
thành MỘT tin nhắn tự nhiên, mạch lạc gửi khách.
QUY TẮC:
- Chỉ dùng nội dung trong các phần trả lời; KHÔNG thêm thông tin, số liệu, giá, ngày, lời hứa hay gợi ý mới.
- Giữ nguyên mọi con số, giá (đúng định dạng), tên xe, ngày giờ, số điện thoại. Được lược bớt ý trùng lặp.
- Không để lộ tên trường kỹ thuật hay mã nội bộ (price_as_of, ID sản phẩm); viết "giá áp dụng từ ngày ...".
- Nếu nhiều phần cùng kết bằng câu hỏi, chỉ giữ MỘT câu hỏi tiếp theo quan trọng nhất, đặt ở cuối.
- Văn bản thuần: KHÔNG dùng markdown (không *, **, #, `). Danh sách dùng "- " ở đầu dòng.
- Giữ một cách xưng hô thống nhất trong cả tin nhắn, theo phần trả lời đầu tiên.
CÁCH LÀM: gọi submit_reply với tin nhắn hoàn chỉnh. Nếu bị từ chối, sửa đúng các lỗi được trả về rồi gọi lại.
Khi được chấp nhận thì dừng, không viết thêm.
""" + STYLE_RULES

TOOLS = [{"name": "submit_reply", "description": "Gửi tin nhắn cuối cùng cho khách; hệ thống kiểm tra trước khi chấp nhận.",
          "input_schema": {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}}]

# "Chào Khang," / "Xin chào bạn!" / "Chào anh/chị." ở đầu câu trả lời
GREETING_PREFIX = re.compile(r"^\s*(?:xin\s+)?chào(?:\s+[^\s,.!\n]+){0,3}\s*[,.!]+\s*", re.IGNORECASE)
MARKDOWN = re.compile(r"[*`]|^\s*#+\s", re.MULTILINE)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
NUMBER = re.compile(r"\d[\d.,:/]*\d|\d")


def strip_greeting(text: str) -> str:
    """Bỏ lời chào ở đầu câu trả lời (dùng khi hội thoại đã bắt đầu)."""
    stripped = GREETING_PREFIX.sub("", text, count=1)
    if not stripped.strip() or stripped == text:
        return text
    return stripped[0].upper() + stripped[1:]


def _plain(text: str) -> str:
    """Markdown -> plain text: bullets become "- ", emphasis and headings are dropped."""
    text = re.sub(r"^(\s*)[*•]\s+", r"\1- ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    return re.sub(r"[*`]", "", text)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in NUMBER.findall(LIST_MARKER.sub("", text))}


def _problems(draft: str, sources: str, executed: bool) -> list[str]:
    """Rule violations the rewrite introduced; the sources were already validated."""
    problems = []
    if not draft:
        problems.append("Tin nhắn rỗng.")
    if MARKDOWN.search(draft):
        problems.append("Có ký tự markdown (*, #, `); viết văn bản thuần.")
    if FORBIDDEN.search(draft) and not FORBIDDEN.search(sources):
        problems.append("Có lời hứa giảm giá/quà/cam kết không có trong phần trả lời.")
    if not executed and any(claim in draft.casefold() for claim in CLAIMS):
        problems.append("Nói đã đặt lịch/làm hợp đồng trong khi hệ thống chưa thực hiện.")
    if new := _numbers(draft) - _numbers(sources):
        problems.append(f"Số liệu không có trong phần trả lời: {', '.join(sorted(new))}.")
    return problems


def _rewrite(state: AgentState, texts: list[str], executed: bool) -> str:
    sources = "\n\n".join(texts)
    if len(texts) == 1 and not MARKDOWN.search(sources):
        return sources  # already one plain answer; a rewrite would only add latency
    accepted: dict[str, str] = {}

    def submit(name: str, args: dict) -> dict:
        draft = str(args.get("reply", "")).strip()
        problems = _problems(draft, sources, executed)
        if not problems:
            accepted.setdefault("reply", draft)
        return {"accepted": not problems, "problems": problems}

    parts = "\n\n".join(f"[Phần {i}]\n{text}" for i, text in enumerate(texts, 1))
    generate_with_tools(COMPOSE_PROMPT, f"Tin nhắn của khách: {state.get('user_text', '')}\n\nCác phần trả lời:\n{parts}",
                        TOOLS, submit, history=state.get("history"), max_turns=3)
    return accepted.get("reply") or _plain(sources)


def compose(state: AgentState) -> dict:
    answers = sorted(state.get("answers", []), key=lambda a: a.get("order", 0))
    usable = [a for a in answers if not a.get("needs_human") and a.get("text", "").strip()]
    interpretation = state.get("conversation_interpretation")
    greeted = bool(interpretation and any(task.domain == Intent.GREETING for task in interpretation.tasks))
    texts = [a["text"].strip() if greeted else strip_greeting(a["text"].strip()) for a in usable]
    reply = _rewrite(state, texts, any(a.get("executed") for a in usable)) if texts else ""
    update: dict = {"draft_reply": reply if greeted else strip_greeting(reply), "trace": ["compose"]}
    reasons = [a.get("handover_reason") or a["agent"].upper() for a in answers if a.get("needs_human")]
    if reasons:
        update.update(needs_human=True, handover_reason="; ".join(dict.fromkeys(reasons)))
    return update
