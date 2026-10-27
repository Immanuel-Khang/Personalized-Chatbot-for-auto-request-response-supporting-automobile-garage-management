"""Book appointments using only canonical known facts and workflow state."""
import json
import uuid

from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import (
    ActiveWorkflow, FactSource, InteractionKind, InteractionStatus, KnownFact,
    PendingInteraction, ServiceType, WorkflowStatus,
)
from app.identity import normalize_phone
from app.services import get_services


APPOINTMENT_SYSTEM_PROMPT = """\
Bạn là chuyên viên đặt lịch của xưởng Foton chuyên nghiệp, thân thiện.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. Xin thông tin còn thiếu một cách tự nhiên, không rập khuôn.
3. PHẢI xác nhận/ghi nhận thông tin khách vừa cung cấp trước khi hỏi tiếp.
4. KHÔNG tự bịa thông tin khách chưa cung cấp.
5. Khi khách nói về sản phẩm/xe, ghi nhận và tiếp tục hỏi thông tin đặt lịch còn thiếu.
6. Chỉ hỏi thông tin trong danh sách còn thiếu, KHÔNG hỏi thêm.
""" + STYLE_RULES

ASK = {
    "service_type": "Bạn muốn đặt lịch bảo dưỡng, sửa chữa, lái thử hay xem xe tại showroom?",
    "appointment_when": "Bạn muốn đến vào ngày giờ nào?",
    "customer_phone": "Bạn cho mình xin số điện thoại để xác nhận lịch nhé?",
}
FIELD_LABELS = {
    "service_type": "Loại dịch vụ (bảo dưỡng/sửa chữa/lái thử/xem xe)",
    "appointment_when": "Ngày giờ hẹn",
    "customer_phone": "Số điện thoại",
}
LABELS: dict[ServiceType, str] = {
    ServiceType.MAINTENANCE: "bảo dưỡng",
    ServiceType.REPAIR: "sửa chữa",
    ServiceType.TEST_DRIVE: "lái thử",
    ServiceType.SHOWROOM_VISIT: "xem xe trực tiếp tại showroom",
}
CONFIRM = "Em ghi nhận lịch {label} ({when}) cho anh/chị. Anh/chị xác nhận đặt lịch này giúp em nhé?"
PHONE_INVALID = "Số điện thoại anh/chị vừa gửi chưa đúng định dạng (10 số, bắt đầu bằng 0), anh/chị kiểm tra lại giúp em nhé."
BOOKING_FAILED = ("Hiện em chưa xác nhận được lịch {when} trên hệ thống ạ. Em vẫn giữ thông tin anh/chị đã cung cấp, "
                  "anh/chị có thể nhắn lại để em thử lại hoặc gọi hotline {hotline} để nhân viên hỗ trợ.")


BOOKING_KEYS = {"service_type", "appointment_when", "service_detail"}
CONTACT_KEYS = {"customer_name", "customer_phone"}


def _value(facts: dict, key: str):
    fact = facts.get(key)
    return fact.value if isinstance(fact, KnownFact) else fact.get("value") if isinstance(fact, dict) else None


def _persist(facts: dict, key: str, value) -> None:
    facts[key] = KnownFact(key=key, value=value, source=FactSource.USER_EXPLICIT)


def _booking_detail(state: AgentState, facts: dict) -> str | None:
    """Vehicle/work item for staff: the product an appointment task referenced, else car_model; plus service_detail."""
    interpretation = state.get("conversation_interpretation")
    entity_ids = [t.entity_id for t in (interpretation.tasks if interpretation else [])
                  if t.domain.value == "APPOINTMENT" and t.entity_id is not None]
    shown = {item.get("entity_id"): item.get("name") for item in state.get("selected_entities") or []
             if isinstance(item, dict) and item.get("entity_type") == "PRODUCT"}
    vehicle = next((shown[i] for i in entity_ids if shown.get(i)), None) or _value(facts, "car_model")
    if not vehicle and (workflow := state.get("active_workflow")):
        collected = workflow.collected_facts if isinstance(workflow, ActiveWorkflow) else workflow.get("collected_facts", {})
        vehicle = collected.get("vehicle")
    parts = [f"Xe: {vehicle}" if vehicle else None,
             f"Hạng mục: {_value(facts, 'service_detail')}" if _value(facts, "service_detail") else None]
    return "\n".join(p for p in parts if p) or None


def _open_pending(state: AgentState) -> list[PendingInteraction]:
    result = []
    for item in state.get("pending_interactions", []) or []:
        pending = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if pending.status == InteractionStatus.OPEN and pending.metadata.get("workflow") == "appointment":
            result.append(pending)
    return result


def _missing(facts: dict) -> list[str]:
    return [key for key in ("service_type", "appointment_when", "customer_phone") if not _value(facts, key)]


def _ask_contextual(state: AgentState, missing: list[str], collected: dict) -> str:
    """Generate contextual question using LLM, with fallback to static."""
    user_message = state.get("task_text") or state.get("user_text", "")
    missing_labels = [FIELD_LABELS[key] for key in missing if key in FIELD_LABELS]
    collected_display = {FIELD_LABELS.get(k, k): v for k, v in collected.items() if v is not None}

    prompt = (
        f"Tin nhắn của khách: {user_message}\n\n"
        f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(collected_display, ensure_ascii=False)}\n\n"
        f"⚠️ THÔNG TIN CÒN THIẾU: {', '.join(missing_labels)}\n\n"
        f"Hãy xác nhận/ghi nhận thông tin khách vừa cung cấp (nếu có), "
        f"sau đó hỏi thông tin còn thiếu đầu tiên một cách tự nhiên."
    )

    llm_reply = generate(APPOINTMENT_SYSTEM_PROMPT, prompt, history=state.get("history"))
    return llm_reply or ASK[missing[0]]


def _new_pending(state: AgentState, question: str, missing: list[str], kind=InteractionKind.QUESTION):
    existing = [item.model_copy(update={"status": InteractionStatus.EXPIRED}) for item in _open_pending(state)]
    existing.append(PendingInteraction(
        interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=kind, text=question,
        status=InteractionStatus.OPEN, created_at_message_id=None,
        metadata={"workflow": "appointment", "missing_slots": missing},
    ))
    return existing


def _contact_correction_for_booked(state: AgentState, workflow: ActiveWorkflow | None) -> str | None:
    """Reply when the customer only corrects name/phone after a request was already stored.
    The DB rows (customers, the pending appointment's contact_phone) are synced by conversation.handle_message."""
    interpretation = state.get("conversation_interpretation")
    if not (workflow and workflow.workflow_type == "appointment"
            and workflow.status == WorkflowStatus.WAITING_FOR_APPROVAL
            and workflow.collected_facts.get("appointment_id") and interpretation):
        return None
    keys = {update.key for update in interpretation.slot_updates}
    if not keys or not keys <= CONTACT_KEYS or keys & BOOKING_KEYS:
        return None
    booked = workflow.collected_facts
    if "customer_phone" in (state.get("rejected_slots") or []):
        return PHONE_INVALID
    return (f"Dạ em đã cập nhật thông tin liên hệ cho yêu cầu đặt lịch {booked.get('service_type')} "
            f"({booked.get('appointment_when')}) đang chờ nhân viên xác nhận ạ.")


def appointment(state: AgentState) -> dict:
    facts = dict(state.get("known_facts", {}) or {})
    interpretation = state.get("conversation_interpretation")
    # Slots were already validated and applied to known_facts by preprocessing.
    missing = _missing(facts)
    workflow = state.get("active_workflow")
    # Checkpoint may deserialize as dict; reconstruct if needed
    if isinstance(workflow, dict):
        workflow = ActiveWorkflow(**workflow)
    if reply := _contact_correction_for_booked(state, workflow):
        return {"answers": [answer(state, "appointment", reply)], "known_facts": facts,
                "trace": step(state, "appointment")}
    if not workflow or workflow.workflow_type != "appointment":
        workflow = ActiveWorkflow(
            workflow_id=f"{state.get('session_id', 'session')}_appointment",
            workflow_type="appointment", status=WorkflowStatus.ACTIVE,
            collected_facts={key: _value(facts, key) for key in ("service_type", "appointment_when", "customer_phone") if _value(facts, key)},
        )

    if missing:
        collected = {key: _value(facts, key) for key in ("service_type", "appointment_when", "customer_phone") if _value(facts, key)}
        if "customer_phone" in missing and "customer_phone" in (state.get("rejected_slots") or []):
            # Preprocessing rejected the number the customer just sent; say so instead of asking as if nothing came.
            question = PHONE_INVALID + " " + ASK["customer_phone"]
        else:
            question = _ask_contextual(state, missing, collected)
        return {
            "answers": [answer(state, "appointment", question)],
            "known_facts": facts,
            "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER,
                "collected_facts": {key: _value(facts, key) for key in facts}}),
            "pending_interactions": _new_pending(state, question, missing),
            "trace": step(state, "appointment"),
        }

    label = LABELS.get(ServiceType(str(_value(facts, "service_type"))))
    pending = _open_pending(state)
    confirmation = next((p for p in pending if p.kind == InteractionKind.CONFIRMATION), None)
    explicit = bool(interpretation and any(
        task.domain.value == "APPOINTMENT" and task.relationship.value in {"NEW_REQUEST", "CONTINUE_TASK", "ACCEPT_OFFER"}
        for task in interpretation.tasks
    ))
    accepted = bool(interpretation and interpretation.accepts_offer is True)
    if confirmation and not accepted:
        question = confirmation.text
        return {"answers": [answer(state, "appointment", question)], "known_facts": facts,
                "trace": step(state, "appointment")}
    if not explicit and not accepted:
        question = CONFIRM.format(label=label, when=_value(facts, "appointment_when"))
        return {
            "answers": [answer(state, "appointment", question)],
            "known_facts": facts,
            "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER}),
            "pending_interactions": _new_pending(state, question, [], InteractionKind.CONFIRMATION),
            "trace": step(state, "appointment"),
        }

    phone = normalize_phone(str(_value(facts, "customer_phone")))
    if not phone:
        facts.pop("customer_phone", None)
        question = PHONE_INVALID + " " + ASK["customer_phone"]
        return {"answers": [answer(state, "appointment", question)], "known_facts": facts,
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER}),
                "pending_interactions": _new_pending(state, question, ["customer_phone"]),
                "trace": step(state, "appointment")}

    try:
        result = get_services().booking.create_appointment(
            state.get("session_id", ""), ServiceType(str(_value(facts, "service_type"))),
            str(_value(facts, "appointment_when")), phone, detail=_booking_detail(state, facts),
        )
    except Exception:
        result = None
    if result is None or not result.ok:
        reply = BOOKING_FAILED.format(when=_value(facts, "appointment_when"), hotline=settings.hotline)
        return {"answers": [answer(state, "appointment", reply)], "known_facts": facts,
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER}),
                "trace": step(state, "appointment")}

    when = _value(facts, "appointment_when")
    if result.status != "CONFIRMED":
        reply = (f"Em đã gửi yêu cầu đặt lịch {label} ({when}) lên hệ thống. "
                 f"{result.message or 'Lịch hẹn đang chờ nhân viên xác nhận.'}")
        status = WorkflowStatus.WAITING_FOR_APPROVAL
    else:
        reply = f"Mình đã xác nhận lịch hẹn {label} ({when}). {result.message}"
        status = WorkflowStatus.NONE
    # The request is stored; its slots must not drive a second booking (a later name/phone correction
    # would otherwise re-enter the confirm-and-book path and create a duplicate row).
    for key in BOOKING_KEYS:
        facts.pop(key, None)
    pending_updates = [item.model_copy(update={"status": InteractionStatus.RESOLVED}) for item in pending]
    booked = workflow.model_copy(update={"status": status, "collected_facts": {
        "appointment_id": result.appointment_id, "service_type": label, "appointment_when": when}})
    return {"answers": [answer(state, "appointment", reply, executed=True)], "known_facts": facts,
            "pending_interactions": pending_updates, "active_workflow": booked,
            "trace": step(state, "appointment")}
