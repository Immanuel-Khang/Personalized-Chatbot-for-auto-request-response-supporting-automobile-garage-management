"""[TRACK C] Human Handover: tạo ticket, chuyển mode HUMAN_PENDING, báo khách kèm lý do chuyển.
Phần trả lời hợp lệ của các agent khác trong cùng lượt (đã qua Output Guard) được giữ lại phía trước.
Nếu đang HUMAN_PENDING rồi: ghi thêm vào ticket đang mở, không tạo ticket mới.
Ticket, mode và pending/workflow là quy tắc nghiệp vụ nên do code quyết định; chỉ lời giải thích lý do cho khách
do LLM viết (theo mã lý do + tin nhắn), có câu trung tính dự phòng khi không có LLM hoặc bản nháp vi phạm guard."""
from app.agent.llm import generate
from app.agent.nodes.compose import STYLE_RULES
from app.agent.nodes.output_guard import violation
from app.agent.state import AgentState, fact_value
from app.config import settings
from app.contracts.schemas import ConversationMode, InteractionStatus, WorkflowStatus
from app.services import get_services

HANDOVER_PROMPT = """\
Bạn là trợ lý của xưởng ô tô Foton. Yêu cầu của khách vừa được chuyển (hoặc ghi thêm) cho nhân viên vì bạn không tự
xử lý được. Viết lời nhắn 1-3 câu gửi khách:
- Nói rõ LÝ DO chuyển, cụ thể theo yêu cầu của khách và là giới hạn của bạn, không đổ cho khách. VD: "Quyết định giảm
  giá nằm ngoài khả năng của mình nên mình đã chuyển cho nhân viên", "Vấn đề kỹ thuật này mình chưa rõ nên mình nhờ nhân
  viên hỗ trợ". Dựa vào mã lý do và tin nhắn, KHÔNG nhắc mã lý do.
- Tình trạng "mới": báo nhân viên sẽ tiếp nhận, khách chờ chút và các tin nhắn sau được lưu lại cho nhân viên.
  Tình trạng "ghi thêm": báo đã ghi thêm yêu cầu này vào yêu cầu đang chờ nhân viên.
- Tuyệt đối KHÔNG hứa hay ngầm hứa giảm giá, quà, kết quả hoặc thời gian phản hồi cụ thể. KHÔNG nhắc lại con số
  phần trăm hay số tiền khách nêu (VD nói "việc giảm giá", không nói "giảm 10%").
- Chỉ nói về việc chuyển nhân viên; các phần khác của tin nhắn đã được trả lời riêng, đừng trả lời lại.
- Nếu được yêu cầu xin số điện thoại thì xin ở cuối; nếu có hotline khẩn cấp thì nêu hotline.
""" + STYLE_RULES


def _notice(state: AgentState, reason: str, already_open: bool) -> str:
    """Lời báo chuyển nhân viên cho khách."""
    asks_phone = not fact_value(state.get("known_facts"), "customer_phone")
    context = (f"Tin nhắn của khách: {state.get('user_text', '')}\nMã lý do (nội bộ): {reason}\n"
               f"Tình trạng: {'ghi thêm' if already_open else 'mới'}\n"
               f"Xin số điện thoại: {'có' if asks_phone and not already_open else 'không'}\n"
               f"Hotline khẩn cấp: {settings.hotline if state.get('safety') else 'không cần'}")
    text = generate(HANDOVER_PROMPT, context, history=state.get("history"))
    if text and text.strip() and not violation(text, set()):
        return text.strip()
    if already_open:
        return "Mình đã ghi chú thêm yêu cầu này để nhân viên xử lý luôn khi tiếp nhận, bạn vui lòng chờ chút nhé."
    reply = ("Yêu cầu này cần nhân viên hỗ trợ nên mình đã chuyển cho nhân viên tư vấn, bạn vui lòng chờ trong "
             "giây lát. Các tin nhắn tiếp theo sẽ được lưu để nhân viên tiếp nhận.")
    if asks_phone:
        reply += " Bạn cho mình xin số điện thoại để nhân viên liên hệ nhanh hơn nhé."
    if state.get("safety"):
        reply += f" Trường hợp khẩn cấp về kỹ thuật, bạn gọi hotline {settings.hotline}."
    return reply


def human_handover(state: AgentState) -> dict:
    reason = state.get("handover_reason") or "UNKNOWN"
    already_open = state.get("mode") == ConversationMode.HUMAN_PENDING.value
    if already_open:
        get_services().handover.append(state.get("session_id", ""), reason, note=state.get("user_text", ""))
    else:
        get_services().handover.create(state.get("session_id", ""), reason, summary=state.get("user_text", ""))

    reply = _notice(state, reason, already_open)
    if partial := state.get("draft_reply", "").strip():  # vd sales đã báo giá, knowledge cần người
        reply = f"{partial}\n\n{reply}"
    update: dict = {"draft_reply": reply, "trace": ["human_handover"]}
    if already_open:
        return update

    # Timeout HUMAN_PENDING (không ai nhận sau HANDOVER_TIMEOUT_MINUTES -> nhắn hotline) xử lý ở conversation.py
    pending = [item.model_copy(update={"status": InteractionStatus.EXPIRED})
               if item.status == InteractionStatus.OPEN else item for item in state.get("pending_interactions") or []]
    workflow = state.get("active_workflow")
    if workflow and workflow.status in (WorkflowStatus.ACTIVE, WorkflowStatus.WAITING_FOR_USER):
        workflow = workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL})
    update.update(set_mode=ConversationMode.HUMAN_PENDING.value, pending_interactions=pending, active_workflow=workflow)
    return update
