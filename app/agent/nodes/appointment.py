"""Appointment Agent: book a service or showroom visit from canonical known facts.

Slots arrive validated from Preprocessing. Booking is a business action, so the decision to call
BookingService stays in code: ask for missing slots, confirm unless the customer explicitly asked,
then book and report only what the service returned.
"""
import json
import uuid

from app.agent.llm import generate
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending, pronoun_instruction, pronoun_vars
from app.config import settings
from app.contracts.schemas import (
    ActiveWorkflow, InteractionKind, InteractionStatus, PendingInteraction, ServiceType, WorkflowStatus,
)
from app.identity import normalize_phone
from app.services import get_services


APPOINTMENT_SYSTEM_PROMPT_TEMPLATE = """\
Bạn là chuyên viên đặt lịch của xưởng Foton chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Xin thông tin còn thiếu một cách tự nhiên, không rập khuôn.
3. PHẢI xác nhận/ghi nhận thông tin khách vừa cung cấp trước khi hỏi tiếp.
4. KHÔNG tự bịa thông tin khách chưa cung cấp.
5. Khi khách nói về sản phẩm/xe, ghi nhận và tiếp tục hỏi thông tin đặt lịch còn thiếu.
6. Chỉ hỏi thông tin trong danh sách còn thiếu, KHÔNG hỏi thêm.
"""

REQUIRED_TEMPLATES = {
    "service_type": ("Loại dịch vụ (bảo dưỡng/sửa chữa/xem xe)",
                     "{customer_cap} muốn đặt lịch bảo dưỡng, sửa chữa hay xem xe tại showroom?"),
    "appointment_when": ("Ngày giờ hẹn", "{customer_cap} muốn đến vào ngày giờ nào?"),
    "customer_phone": ("Số điện thoại", "{customer_cap} cho {bot} xin số điện thoại để xác nhận lịch nhé?"),
}
LABELS = {ServiceType.MAINTENANCE: "bảo dưỡng", ServiceType.REPAIR: "sửa chữa",
          ServiceType.SHOWROOM_VISIT: "xem xe trực tiếp tại showroom"}
CONFIRM_TEMPLATE = "{bot_cap} ghi nhận lịch {{label}} ({{when}}) cho {customer}. {customer_cap} xác nhận đặt lịch này giúp {bot} nhé?"
PHONE_INVALID_TEMPLATE = "Số điện thoại {customer} vừa gửi chưa đúng định dạng (10 số, bắt đầu bằng 0), {customer} kiểm tra lại giúp {bot} nhé."
BOOKING_FAILED_TEMPLATE = ("Hiện {bot} chưa xác nhận được lịch {{when}} trên hệ thống ạ. {bot_cap} vẫn giữ thông tin {customer} đã cung cấp, "
                          "{customer} có thể nhắn lại để {bot} thử lại hoặc gọi hotline {{hotline}} để nhân viên hỗ trợ.")
BOOKING_KEYS = {"service_type", "appointment_when", "service_detail"}
CONTACT_KEYS = {"customer_name", "customer_phone"}
EXPLICIT = {"NEW_REQUEST", "CONTINUE_TASK", "ACCEPT_OFFER"}


def _required(p: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {key: (label, question.format(**p)) for key, (label, question) in REQUIRED_TEMPLATES.items()}


def _booking_detail(state: AgentState, facts: dict) -> tuple[str | None, str | None]:
    """(vehicle, work item) for staff: the product an appointment task referenced, else car_model, else the product
    the customer was last talking about in sales; plus service_detail."""
    interpretation = state.get("conversation_interpretation")
    shown = {item.get("entity_id"): item.get("name") for item in state.get("selected_entities") or []
             if isinstance(item, dict) and item.get("entity_type") == "PRODUCT"}
    vehicle = next((shown[t.entity_id] for t in (interpretation.tasks if interpretation else [])
                    if t.domain.value == "APPOINTMENT" and shown.get(t.entity_id)), None)
    vehicle = vehicle or fact_value(facts, "car_model") or (state.get("focused_product") or {}).get("name")
    return vehicle, fact_value(facts, "service_detail")


def _note(vehicle: str | None, detail: str | None) -> str | None:
    parts = [f"Xe: {vehicle}" if vehicle else None, f"Hạng mục: {detail}" if detail else None]
    return "\n".join(p for p in parts if p) or None


def _booked_recap(state: AgentState, workflow: ActiveWorkflow | None) -> str | None:
    """A request is already stored and this turn brings no new booking slots: answer from the stored request
    instead of re-collecting service/time (its slots were cleared after booking to prevent duplicates)."""
    if not (workflow and workflow.workflow_type == "appointment" and workflow.collected_facts.get("appointment_id")):
        return None
    interpretation = state.get("conversation_interpretation")
    if interpretation and any(update.key in BOOKING_KEYS for update in interpretation.slot_updates):
        return None  # new service/time -> a new booking request
    booked, p = workflow.collected_facts, pronoun_vars(state)
    vehicle = booked.get("vehicle")
    car = (f", xe {vehicle}" if vehicle else
           f". Yêu cầu này chưa ghi mẫu xe cụ thể, {p['customer']} muốn xem mẫu nào thì nhắn {p['bot']} để bổ sung nhé")
    status = ("đang chờ nhân viên xác nhận" if workflow.status == WorkflowStatus.WAITING_FOR_APPROVAL
              else "đã được xác nhận")
    return (f"Dạ yêu cầu đặt lịch {booked.get('service_type')} của {p['customer']} ({booked.get('appointment_when')}) "
            f"{status}{car}.")


def _ask(state: AgentState, missing: list[str], facts: dict) -> str:
    """Ask for the first missing slot in context; a just-rejected phone is reported as invalid."""
    p = pronoun_vars(state)
    REQUIRED = _required(p)
    if "customer_phone" in missing and "customer_phone" in (state.get("rejected_slots") or []):
        return f"{PHONE_INVALID_TEMPLATE.format(**p)} {REQUIRED['customer_phone'][1]}"

    collected = {REQUIRED[k][0]: fact_value(facts, k) for k in REQUIRED if k not in missing}
    system_prompt = APPOINTMENT_SYSTEM_PROMPT_TEMPLATE + STYLE_RULES.format(
        pronoun_instruction=pronoun_instruction(p["customer"], p["bot"]))

    prompt = (f"Tin nhắn của khách: {state.get('task_text') or state.get('user_text', '')}\n\n"
              f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(collected, ensure_ascii=False)}\n\n"
              f"⚠️ THÔNG TIN CÒN THIẾU: {', '.join(REQUIRED[k][0] for k in missing)}\n\n"
              "Hãy xác nhận/ghi nhận thông tin khách vừa cung cấp (nếu có), "
              "sau đó hỏi thông tin còn thiếu đầu tiên một cách tự nhiên.")
    return generate(system_prompt, prompt, history=state.get("history")) or REQUIRED[missing[0]][1]


def _with_pending(state: AgentState, text: str, missing: list[str], kind=InteractionKind.QUESTION) -> list:
    """Expire this workflow's open interactions and open a new one."""
    expired = [item.model_copy(update={"status": InteractionStatus.EXPIRED}) for item in open_pending(state, "appointment")]
    return [*expired, PendingInteraction(
        interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=kind, text=text, status=InteractionStatus.OPEN,
        metadata={"workflow": "appointment", "missing_slots": missing})]


def _contact_update_after_booking(state: AgentState, workflow: ActiveWorkflow | None) -> str | None:
    """Reply when the customer only corrects name/phone after the request was stored.
    The DB rows (customers, the pending appointment's contact_phone) are synced by conversation.handle_message."""
    interpretation = state.get("conversation_interpretation")
    if not (interpretation and workflow and workflow.workflow_type == "appointment"
            and workflow.status == WorkflowStatus.WAITING_FOR_APPROVAL and workflow.collected_facts.get("appointment_id")):
        return None
    keys = {update.key for update in interpretation.slot_updates}
    if not keys or not keys <= CONTACT_KEYS:
        return None
    p = pronoun_vars(state)
    if "customer_phone" in (state.get("rejected_slots") or []):
        return PHONE_INVALID_TEMPLATE.format(**p)
    booked = workflow.collected_facts
    return (f"Dạ {p['bot']} đã cập nhật thông tin liên hệ cho yêu cầu đặt lịch {booked.get('service_type')} "
            f"({booked.get('appointment_when')}) đang chờ nhân viên xác nhận ạ.")


def appointment(state: AgentState) -> dict:
    facts = dict(state.get("known_facts") or {})
    workflow = state.get("active_workflow")
    p = pronoun_vars(state)

    def reply(text: str, executed: bool = False, **changes) -> dict:
        return {"answers": [answer(state, "appointment", text, executed=executed)], "known_facts": facts,
                "trace": ["appointment"], **changes}

    if text := _contact_update_after_booking(state, workflow) or _booked_recap(state, workflow):
        return reply(text)
    if workflow and workflow.collected_facts.get("appointment_id"):
        workflow = None  # new service/time after a stored request: start a separate booking
    if not workflow or workflow.workflow_type != "appointment":
        workflow = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_appointment",
                                  workflow_type="appointment", status=WorkflowStatus.ACTIVE)
    current: ActiveWorkflow = workflow

    def waiting(**extra) -> ActiveWorkflow:
        return current.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER, **extra})

    phone = normalize_phone(str(fact_value(facts, "customer_phone") or ""))
    if not phone:
        facts.pop("customer_phone", None)
    missing = [key for key in REQUIRED_TEMPLATES if not fact_value(facts, key)]
    if missing or not phone:
        question = _ask(state, missing, facts)
        return reply(question, active_workflow=waiting(collected_facts={k: fact_value(facts, k) for k in facts}),
                     pending_interactions=_with_pending(state, question, missing))

    service, when = ServiceType(str(fact_value(facts, "service_type"))), fact_value(facts, "appointment_when")
    label = LABELS[service]
    interpretation = state.get("conversation_interpretation")
    accepted = bool(interpretation and interpretation.accepts_offer is True)
    explicit = bool(interpretation and any(task.domain.value == "APPOINTMENT" and task.relationship.value in EXPLICIT
                                           for task in interpretation.tasks))
    pending = open_pending(state, "appointment")
    confirmation = next((p for p in pending if p.kind == InteractionKind.CONFIRMATION), None)
    if confirmation and not accepted:
        return reply(confirmation.text)
    if not (explicit or accepted):
        question = CONFIRM_TEMPLATE.format(**p).format(label=label, when=when)
        return reply(question, active_workflow=waiting(),
                     pending_interactions=_with_pending(state, question, [], InteractionKind.CONFIRMATION))

    vehicle, detail = _booking_detail(state, facts)
    try:
        result = get_services().booking.create_appointment(state.get("session_id", ""), service, str(when), phone,
                                                           detail=_note(vehicle, detail))
    except Exception:
        result = None
    if result is None or not result.ok:
        return reply(BOOKING_FAILED_TEMPLATE.format(**p).format(when=when, hotline=settings.hotline), active_workflow=waiting())

    if result.status == "CONFIRMED":
        text = f"{p['bot_cap']} đã xác nhận lịch hẹn {label} ({when}). {result.message}"
        status = WorkflowStatus.NONE
    else:
        text = (f"{p['bot_cap']} đã gửi yêu cầu đặt lịch {label} ({when}{', xe ' + vehicle if vehicle else ''}) lên hệ thống. "
                f"{result.message or 'Lịch hẹn đang chờ nhân viên xác nhận.'}")
        status = WorkflowStatus.WAITING_FOR_APPROVAL
    # The request is stored; its slots must not drive a second booking (a later name/phone correction
    # would otherwise re-enter the confirm-and-book path and create a duplicate row).
    for key in BOOKING_KEYS:
        facts.pop(key, None)
    booked = workflow.model_copy(update={"status": status, "collected_facts": {
        "appointment_id": result.appointment_id, "service_type": label, "appointment_when": when, "vehicle": vehicle}})
    return reply(text, executed=True, active_workflow=booked,
                 pending_interactions=[p.model_copy(update={"status": InteractionStatus.RESOLVED}) for p in pending])
