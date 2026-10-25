"""Book appointments using only canonical known facts and workflow state."""
import json
import uuid

from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.nodes.service_type import LABELS, clarify_question, resolve_service_type
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
CONFIRM = "Em ghi nhận lịch {label} ({when}) cho anh/chị. Anh/chị xác nhận đặt lịch này giúp em nhé?"
PHONE_INVALID = "Số điện thoại anh/chị vừa gửi chưa đúng định dạng (10 số, bắt đầu bằng 0), anh/chị kiểm tra lại giúp em nhé."
BOOKING_FAILED = ("Hiện em chưa xác nhận được lịch {when} trên hệ thống ạ. Em vẫn giữ thông tin anh/chị đã cung cấp, "
                  "anh/chị có thể nhắn lại để em thử lại hoặc gọi hotline {hotline} để nhân viên hỗ trợ.")


def _value(facts: dict, key: str):
    fact = facts.get(key)
    return fact.value if isinstance(fact, KnownFact) else fact.get("value") if isinstance(fact, dict) else None


def _persist(facts: dict, key: str, value) -> None:
    facts[key] = KnownFact(key=key, value=value, source=FactSource.USER_EXPLICIT)


def _open_pending(state: AgentState) -> list[PendingInteraction]:
    result = []
    for item in state.get("pending_interactions", []) or []:
        pending = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if pending.status == InteractionStatus.OPEN and pending.metadata.get("workflow") == "appointment":
            result.append(pending)
    return result


def _missing(facts: dict) -> list[str]:
    return [key for key in ("service_type", "appointment_when", "customer_phone") if not _value(facts, key)]


def _ask_text(missing: list[str], detection) -> str:
    if "service_type" in missing and detection.ambiguous:
        return clarify_question(detection.candidates)
    return ASK[missing[0]]


def _ask_contextual(state: AgentState, missing: list[str], detection, collected: dict) -> str:
    """Generate contextual question using LLM, with fallback to static."""
    if "service_type" in missing and detection.ambiguous:
        return clarify_question(detection.candidates)

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


def appointment(state: AgentState) -> dict:
    facts = dict(state.get("known_facts", {}) or {})
    interpretation = state.get("conversation_interpretation")
    text = state.get("task_text") or state.get("user_text", "")
    for update in interpretation.slot_updates if interpretation else []:
        if update.key in {"service_type", "appointment_when", "when_text", "customer_phone", "phone", "service_detail"}:
            if update.operation.value == "CLEAR":
                facts.pop(update.key, None)
            else:
                key = "when_text" if update.key == "appointment_when" else "customer_phone" if update.key == "phone" else update.key
                _persist(facts, key, update.value)
    if "customer_phone" not in facts and (value := _value(facts, "phone")):
        _persist(facts, "customer_phone", value)
    if "appointment_when" not in facts and (value := _value(facts, "when_text")):
        _persist(facts, "appointment_when", value)

    current_service = _value(facts, "service_type")
    detection = resolve_service_type(text, current_service)
    if detection.service:
        _persist(facts, "service_type", detection.service.value)
    missing = _missing(facts)
    workflow = state.get("active_workflow")
    # Checkpoint may deserialize as dict; reconstruct if needed
    if isinstance(workflow, dict):
        workflow = ActiveWorkflow(**workflow)
    if not workflow or workflow.workflow_type != "appointment":
        workflow = ActiveWorkflow(
            workflow_id=f"{state.get('session_id', 'session')}_appointment",
            workflow_type="appointment", status=WorkflowStatus.ACTIVE,
            collected_facts={key: _value(facts, key) for key in ("service_type", "appointment_when", "customer_phone") if _value(facts, key)},
        )

    if missing:
        collected = {key: _value(facts, key) for key in ("service_type", "appointment_when", "customer_phone") if _value(facts, key)}
        question = _ask_contextual(state, missing, detection, collected)
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
            str(_value(facts, "appointment_when")), phone,
        )
    except Exception:
        result = None
    if result is None or not result.ok:
        reply = BOOKING_FAILED.format(when=_value(facts, "appointment_when"), hotline=settings.hotline)
        return {"answers": [answer(state, "appointment", reply)], "known_facts": facts,
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER}),
                "trace": step(state, "appointment")}

    if result.status != "CONFIRMED":
        reply = result.message or "Yêu cầu đặt lịch đã được ghi nhận và đang chờ xác nhận từ nhân viên ạ."
        status = WorkflowStatus.WAITING_FOR_APPROVAL
    else:
        reply = f"Mình đã xác nhận lịch hẹn {label} ({_value(facts, 'appointment_when')}). {result.message}"
        status = WorkflowStatus.NONE
        for key in ("service_type", "appointment_when", "customer_phone", "when_text", "phone", "service_detail"):
            facts.pop(key, None)
    pending_updates = [item.model_copy(update={"status": InteractionStatus.RESOLVED}) for item in pending]
    return {"answers": [answer(state, "appointment", reply)], "known_facts": facts,
            "pending_interactions": pending_updates, "active_workflow": workflow.model_copy(update={"status": status}),
            "trace": step(state, "appointment")}
