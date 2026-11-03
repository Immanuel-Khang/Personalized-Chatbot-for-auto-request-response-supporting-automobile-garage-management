"""Appointment Agent: create, reschedule, cancel or look up the session's appointments through four tools.

Agent -> tool -> BookingService (PostgreSQL) -> tool result -> agent -> customer reply. The LLM picks the tool;
business rules stay in code: a write needs every required slot and runs only when the customer accepted, on the
very next message, the confirmation for that exact action. Otherwise the tool returns NEEDS_CONFIRMATION (or
MISSING) and the agent asks. The reply is grounded in what the service returned this turn.
"""
import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

from app.agent.llm import generate_with_tools
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending, pronoun_instruction, pronoun_vars
from app.config import settings
from app.contracts.schemas import (
    ActiveWorkflow, BookingResult, InteractionKind, InteractionStatus, PendingInteraction, ServiceType,
    TaskRelationship, WorkflowStatus,
)
from app.identity import is_full_name, normalize_phone
from app.services import get_services

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Bạn là chuyên viên lịch hẹn của xưởng Foton (bảo dưỡng, sửa chữa, xem xe tại showroom).
CÔNG CỤ:
- get_appointment_details: các lịch hẹn của khách (thời gian, trạng thái). Gọi trước khi trả lời câu hỏi về lịch hẹn
  và trước khi đổi/hủy để lấy đúng appointment_id.
- create_appointment: đặt lịch mới từ thông tin đặt lịch đã biết.
- reschedule_appointment: đổi lịch appointment_id sang thời gian mới.
- cancel_appointment: hủy lịch appointment_id.
QUY TẮC:
1. Tiếng Việt, ngắn gọn; chỉ dựa trên kết quả công cụ của lượt này. KHÔNG bịa giờ, tự suy luận ý muốn đặt lịch của khách, trạng thái, mã lịch.
2. Kết quả status=NEEDS_CONFIRMATION: nêu đúng nội dung "proposal" và hỏi khách xác nhận; CHƯA được nói là đã làm.
   status=MISSING: ghi nhận thông tin khách vừa đưa rồi hỏi thông tin còn thiếu đầu tiên.
   ok=true: báo đã thực hiện theo "message". ok=false: báo chưa thực hiện được theo "message", gợi ý hotline {hotline}.
3. "Khách vừa đồng ý" = có: gọi lại đúng công cụ của thao tác đang chờ xác nhận để thực hiện.
4. Khách có nhiều lịch đang hiệu lực mà không rõ muốn đổi/hủy lịch nào: liệt kê ngắn và hỏi, KHÔNG tự chọn.
5. Trạng thái: dùng "status_label"; thời gian: dùng "when_label". Lịch chưa có start_time thì giờ chính xác do nhân
   viên chốt khi xác nhận.
6. Khách chỉ cập nhật tên/SĐT: ghi nhận thông tin liên hệ mới, không gọi công cụ.
7. Chỉ quy đổi từ "Thời gian khách đã nêu" ra ngày cụ thể (YYYY-MM-DD) theo "Hôm nay" rồi gọi công cụ ngay. Khách
   chưa nêu thời gian: gọi create_appointment KHÔNG kèm appointment_date rồi hỏi khách muốn ngày nào; KHÔNG tự chọn
   ngày (kể cả hôm nay) và KHÔNG nhắc ngày nào trong câu trả lời. Tuần tính từ
   thứ 2 đến chủ nhật; "tuần sau" là tuần kế tiếp; ngày tính ra đã qua thì lấy ngày đó của tuần kế tiếp.
   start_time (HH:MM) chỉ khi khách nêu giờ cụ thể ("8h", "14 giờ 30"); chỉ có ngày hoặc buổi (sáng/chiều) thì bỏ
   trống start_time và KHÔNG hỏi thêm giờ. Công cụ báo INVALID_DATE thì quy đổi lại.
8. Tra cứu lịch hẹn hoặc sau khi thao tác thành công: trả lời đúng việc đó rồi dừng, KHÔNG hỏi thêm thông tin
   (loại xe, biển số...) hay đề nghị việc khác.
9. Xưng hô: chỉ dùng đúng cặp xưng hô trong quy tắc phong cách bên dưới, KHÔNG trộn (VD "Mình" với "chị/em").
"""

_WHEN = {"appointment_date": {"type": "string", "description": "Ngày hẹn YYYY-MM-DD, quy đổi từ lời khách"},
         "start_time": {"type": "string", "description": "Giờ bắt đầu HH:MM; bỏ trống nếu khách chưa nêu giờ cụ thể"}}
_ID = {"appointment_id": {"type": "integer", "description": "id lịch hẹn lấy từ get_appointment_details"}}
TOOLS = [
    {"name": "get_appointment_details", "description": "Xem các lịch hẹn của khách (mới nhất trước).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "create_appointment", "description": "Đặt lịch hẹn mới từ thông tin đặt lịch đã biết.",
     "input_schema": {"type": "object", "required": [], "properties": _WHEN}},
    {"name": "reschedule_appointment", "description": "Đổi lịch hẹn sang ngày giờ mới.",
     "input_schema": {"type": "object", "required": ["appointment_id", "appointment_date"],
                      "properties": {**_ID, **_WHEN}}},
    {"name": "cancel_appointment", "description": "Hủy lịch hẹn.",
     "input_schema": {"type": "object", "required": ["appointment_id"], "properties": _ID}},
]
WRITE_TOOLS = {"create_appointment", "reschedule_appointment", "cancel_appointment"}
REQUIRED = {"service_type": "loại dịch vụ (bảo dưỡng/sửa chữa/xem xe)", "appointment_when": "ngày giờ hẹn",
            "customer_name": "họ và tên đầy đủ", "customer_phone": "số điện thoại"}
LABELS = {ServiceType.MAINTENANCE.value: "bảo dưỡng", ServiceType.REPAIR.value: "sửa chữa",
          ServiceType.SHOWROOM_VISIT.value: "xem xe tại showroom"}
STATUS_LABELS = {"PENDING_APPROVAL": "đang chờ nhân viên xác nhận", "CONFIRMED": "đã được xác nhận",
                 "CANCELLED": "đã hủy", "REJECTED": "không được duyệt", "DONE": "đã hoàn thành"}
BOOKING_KEYS = {"service_type", "appointment_when", "service_detail"}
LOCAL_TZ = timezone(timedelta(hours=7))
WEEKDAYS = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")
MAX_DAYS_AHEAD = 180
# A day the customer never stated ("hôm nay", "ngày mai", "02-11-2026", "Thứ 2") must not appear in the reply.
INVENTED_DATE = re.compile(r"hôm nay|ngày mai|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2}|"
                           r"thứ\s*[2-7]|thứ\s*(?:hai|ba|tư|năm|sáu|bảy)|chủ\s*nhật", re.IGNORECASE)
FAILED = BookingResult(ok=False, status="ERROR", message="Hệ thống chưa thực hiện được thao tác này.")


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


def _accepted(state: AgentState) -> bool:
    interpretation = state.get("conversation_interpretation")
    return bool(interpretation and (interpretation.accepts_offer is True or any(
        task.relationship == TaskRelationship.ACCEPT_OFFER for task in interpretation.tasks)))


def _turn(state: AgentState) -> int:
    """Full history length identifies the turn; the next turn's is two higher. state["history"] is windowed."""
    return state.get("turn", len(state.get("history") or []))


def _new_time_this_turn(state: AgentState) -> bool:
    interpretation = state.get("conversation_interpretation")
    return bool(interpretation and any(u.key == "appointment_when" for u in interpretation.slot_updates))


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _parse_when(raw_date, raw_time) -> tuple[date, time | None] | str:
    """Validate the date/time the LLM deduced; an error string goes back to it as the tool result."""
    try:
        day = date.fromisoformat(str(raw_date or ""))
    except ValueError:
        return "appointment_date phải có dạng YYYY-MM-DD"
    if day < _today():
        return f"ngày {day.isoformat()} đã qua"
    if day > _today() + timedelta(days=MAX_DAYS_AHEAD):
        return f"ngày {day.isoformat()} quá xa (tối đa {MAX_DAYS_AHEAD} ngày)"
    if not raw_time:
        return day, None
    try:
        return day, time.fromisoformat(str(raw_time)).replace(second=0, microsecond=0)
    except ValueError:
        return "start_time phải có dạng HH:MM"


def _fmt(day: str | None, start: str | None) -> str:
    if not day:
        return "chưa có ngày"
    d = date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()]} {d:%d/%m/%Y}" + (f" lúc {start}" if start else "")


def _summary(action: str, args: dict, info: dict | None) -> str:
    """What the customer is asked to confirm, in plain Vietnamese."""
    if action == "create_appointment":
        vehicle = f", xe {args['vehicle']}" if args.get("vehicle") else ""
        return (f"đặt lịch {LABELS[args['service_type']]} ({_fmt(args['date'], args['time'])}{vehicle}) "
                f"cho {args['name']}, SĐT {args['phone']}")
    current = (f"lịch {LABELS.get(info['service_type'], '')} ({_fmt(info['appointment_date'], info['start_time'])})"
               if info else "lịch hẹn")
    if action == "reschedule_appointment":
        return f"đổi {current} sang {_fmt(args['date'], args['time'])}"
    return f"hủy {current}"


def appointment(state: AgentState) -> dict:
    facts = dict(state.get("known_facts") or {})
    session_id = state.get("session_id", "")
    booking = get_services().booking
    p = pronoun_vars(state)
    workflow = state.get("active_workflow")
    mine = open_pending(state, "appointment")
    confirmation = next((item for item in mine if item.kind == InteractionKind.CONFIRMATION
                         and item.metadata.get("turn") == _turn(state) - 2), None)  # a stale one is never accepted
    approved = confirmation.metadata if confirmation and _accepted(state) else None
    vehicle, detail = _booking_detail(state, facts)
    results: list[tuple[str, dict]] = []
    done: dict[str, dict] = {}  # executed writes; a repeated call must not write twice
    proposal: dict = {}
    missing: list[str] = []

    def own(appointment_id) -> dict | None:
        return next((info.model_dump() for info in booking.list_appointments(session_id, active_only=True)
                     if info.id == appointment_id), None)

    def confirm_or_run(action: str, args: dict, matches: bool, info: dict | None, run) -> dict:
        if action in done:
            return done[action]
        if approved and approved.get("action") == action and matches:
            try:
                result = run(approved["args"]).model_dump()  # exactly what the customer confirmed
            except Exception:
                logger.exception("Appointment %s failed", action)
                result = FAILED.model_dump()
            done[action] = result
            return result
        proposal.update(action=action, args=args, text=_summary(action, args, info))
        return {"status": "NEEDS_CONFIRMATION", "proposal": proposal["text"]}

    def when(tool_args: dict) -> tuple[str, str | None] | dict:
        parsed = _parse_when(tool_args.get("appointment_date"), tool_args.get("start_time"))
        if isinstance(parsed, str):
            # The customer already gave a time: converting it is the agent's job, never a question to the customer.
            stated = fact_value(facts, "appointment_when")
            hint = (f" Tự quy đổi '{stated}' theo 'Hôm nay' ra appointment_date YYYY-MM-DD rồi gọi lại công cụ; "
                    "KHÔNG hỏi khách ngày dương lịch." if stated else "")
            return {"status": "INVALID_DATE", "message": parsed + hint}
        return parsed[0].isoformat(), parsed[1].strftime("%H:%M") if parsed[1] else None

    def run_args(a: dict) -> tuple[date, time | None]:
        return date.fromisoformat(a["date"]), time.fromisoformat(a["time"]) if a["time"] else None

    def create(tool_args: dict) -> dict:
        phone = normalize_phone(str(fact_value(facts, "customer_phone") or ""))
        name = str(fact_value(facts, "customer_name") or "")
        present = {"customer_phone": phone, "customer_name": is_full_name(name)}  # a given name alone is not enough
        missing[:] = [label for key, label in REQUIRED.items() if not present.get(key, fact_value(facts, key))]
        if missing:
            if "appointment_when" not in facts:  # never let a date the LLM picked itself reach the customer
                missing[:] = [REQUIRED["appointment_when"], *(m for m in missing if m != REQUIRED["appointment_when"])]
            invalid = "customer_phone" in (state.get("rejected_slots") or [])
            return {"status": "MISSING", "missing": missing,
                    **({"invalid": "số điện thoại chưa đúng định dạng (10 số, bắt đầu bằng 0)"} if invalid else {})}
        if isinstance(deduced := when(tool_args), dict):
            return deduced
        args = {"service_type": str(fact_value(facts, "service_type")), "when": str(fact_value(facts, "appointment_when")),
                "date": deduced[0], "time": deduced[1], "name": name, "phone": phone, "vehicle": vehicle,
                "detail": detail}
        # On "ok" the confirmed date runs even if the LLM re-deduces it differently; the facts must be unchanged.
        matches = approved is not None and all(approved.get("args", {}).get(k) == args[k]
                                               for k in ("service_type", "when", "name", "phone"))
        return confirm_or_run("create_appointment", args, matches, None,
                              lambda a: booking.create_appointment(session_id, ServiceType(a["service_type"]),
                                                                   *run_args(a), a["phone"],
                                                                   detail=_note(a["vehicle"], a["detail"])))

    def change(name: str, tool_args: dict) -> dict:
        appointment_id = tool_args.get("appointment_id")
        info = own(appointment_id)
        if info is None:
            return {"ok": False, "status": "NOT_FOUND", "message": "Không tìm thấy lịch hẹn đang hiệu lực với id này."}
        same_id = approved is not None and approved.get("args", {}).get("appointment_id") == appointment_id
        if name == "cancel_appointment":
            return confirm_or_run(name, {"appointment_id": appointment_id}, same_id, info,
                                  lambda a: booking.cancel_appointment(session_id, a["appointment_id"]))
        if not tool_args.get("appointment_date"):
            missing[:] = ["thời gian mới"]
            return {"status": "MISSING", "missing": missing}
        if isinstance(deduced := when(tool_args), dict):
            return deduced
        args = {"appointment_id": appointment_id, "date": deduced[0], "time": deduced[1]}
        # "Ok" to the proposal keeps the proposed time; a time stated now is a new proposal.
        confirmed = (approved or {}).get("args", {})
        matches = same_id and (not _new_time_this_turn(state)
                               or (confirmed.get("date"), confirmed.get("time")) == deduced)
        return confirm_or_run(name, args, matches, info,
                              lambda a: booking.reschedule_appointment(session_id, a["appointment_id"], *run_args(a)))

    def execute(name: str, tool_args: dict) -> dict:
        try:
            if name == "get_appointment_details":
                result = {"appointments": [{**info.model_dump(), "status_label": STATUS_LABELS.get(info.status, info.status),
                                            "when_label": _fmt(info.appointment_date, info.start_time),
                                            "service_label": LABELS.get(info.service_type, info.service_type)}
                                           for info in booking.list_appointments(session_id)[:5]]}
            elif name == "create_appointment":
                result = create(tool_args)
            elif name in WRITE_TOOLS:
                result = change(name, tool_args)
            else:
                result = {"error": f"unknown tool {name}"}
        except Exception:
            logger.exception("Appointment tool %s failed", name)
            result = FAILED.model_dump()
        results.append((name, result))
        return result

    known = {label: fact_value(facts, key) for key, label in REQUIRED.items() if fact_value(facts, key)}
    if vehicle:
        known["xe"] = vehicle
    waiting_for = f"{confirmation.text} (thao tác: {confirmation.metadata.get('action')})" if confirmation else "không"
    today = _today()
    context = (f"Hôm nay: {WEEKDAYS[today.weekday()]} {today.isoformat()}\n"
               f"Tin nhắn của khách: {state.get('task_text') or state.get('user_text', '')}\n"
               f"Thời gian khách đã nêu: {fact_value(facts, 'appointment_when') or 'chưa nêu'}\n"
               f"Thông tin đặt lịch đã biết: {json.dumps(known, ensure_ascii=False)}\n"
               f"Xác nhận đang chờ: {waiting_for}\nKhách vừa đồng ý xác nhận: {'có' if approved else 'không'}")
    system = SYSTEM_PROMPT.format(hotline=settings.hotline) + STYLE_RULES.format(
        pronoun_instruction=pronoun_instruction(p["customer"], p["bot"]))
    out = generate_with_tools(system, context, TOOLS, execute, history=state.get("history"), max_turns=4)
    reply = (out.get("final_response") or "").strip()
    if not _new_time_this_turn(state) and "appointment_when" not in facts and not results_have_dates(results)             and INVENTED_DATE.search(reply):
        logger.warning("Appointment reply named a date the customer never gave; using the fallback")
        reply = ""
    reply = reply or _fallback(results, proposal, missing, p)
    return _update(state, facts, workflow, mine, reply, results, done, proposal, missing)


def results_have_dates(results: list[tuple[str, dict]]) -> bool:
    """A lookup of stored appointments may legitimately mention their dates."""
    return any(name == "get_appointment_details" and result.get("appointments") for name, result in results)


def _fallback(results: list[tuple[str, dict]], proposal: dict, missing: list[str], p: dict) -> str:
    """Reply from the tool results when the LLM gave no text."""
    if proposal:
        return f"Dạ {p['customer']} xác nhận giúp {p['bot']}: {p['customer']} muốn {proposal['text']} phải không ạ?"
    if missing:
        return f"Dạ {p['customer']} cho {p['bot']} xin {missing[0]} nhé."
    if results and (message := results[-1][1].get("message")):
        return message if results[-1][1].get("ok") else f"{message} {p['customer_cap']} có thể gọi hotline {settings.hotline}."
    return f"Dạ {p['bot']} chưa tìm thấy lịch hẹn nào của {p['customer']} ạ."


def _pending(mine: list[PendingInteraction], status: InteractionStatus, new: PendingInteraction | None = None) -> list:
    return [*(item.model_copy(update={"status": status}) for item in mine), *([new] if new else [])]


def _update(state: AgentState, facts: dict, workflow: ActiveWorkflow | None, mine: list[PendingInteraction],
            reply: str, results: list[tuple[str, dict]], done: dict[str, dict], proposal: dict,
            missing: list[str]) -> dict:
    """State after the turn: open a confirmation/question, or close the workflow once a write succeeded."""
    if not workflow or workflow.workflow_type != "appointment":
        workflow = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_appointment",
                                  workflow_type="appointment", status=WorkflowStatus.ACTIVE)
    succeeded = {name: result for name, result in done.items() if result.get("ok")}
    # Lookups are DB results too, so a reply restating a stored booking is grounded.
    looked_up = any(name == "get_appointment_details" and result.get("appointments") for name, result in results)
    update: dict = {"trace": ["appointment"], "known_facts": facts,
                    "answers": [answer(state, "appointment", reply, executed=bool(succeeded) or looked_up)]}

    if proposal or missing:
        kind = InteractionKind.CONFIRMATION if proposal else InteractionKind.QUESTION
        metadata = ({"workflow": "appointment", "action": proposal["action"], "args": proposal["args"],
                     "turn": _turn(state)} if proposal else {"workflow": "appointment", "missing_slots": missing})
        new = PendingInteraction(interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=kind, text=reply,
                                 status=InteractionStatus.OPEN, metadata=metadata)
        update["pending_interactions"] = _pending(mine, InteractionStatus.EXPIRED, new)
        update["active_workflow"] = workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER})
    elif succeeded:
        update["pending_interactions"] = _pending(mine, InteractionStatus.RESOLVED)
        if "create_appointment" in succeeded:
            for key in BOOKING_KEYS:  # the stored request must not drive a second booking
                facts.pop(key, None)
            update["active_workflow"] = workflow.model_copy(update={
                "status": WorkflowStatus.WAITING_FOR_APPROVAL,
                "collected_facts": {"appointment_id": succeeded["create_appointment"].get("appointment_id")}})
        else:
            facts.pop("appointment_when", None)  # the new time is now stored on the appointment row
            update["active_workflow"] = None
    elif done:  # a confirmed write failed: drop the confirmation, the customer can ask again
        update["pending_interactions"] = _pending(mine, InteractionStatus.EXPIRED)
    return update
