"""Appointment Agent: create, reschedule, cancel or look up the session's appointments through four tools.

Agent -> tool -> BookingService (PostgreSQL) -> tool result -> agent -> customer reply. The LLM picks the tool;
business rules stay in code: a write needs every required slot and runs only when the customer accepted, on the
very next message, the confirmation for that exact action. Otherwise the tool returns NEEDS_CONFIRMATION (or
MISSING) and the agent asks. The reply is grounded in what the service returned this turn.
"""
import json
import logging
import uuid
from datetime import date, time

from app.agent.date_extraction import (NEEDS_PERIOD, OUT_OF_RANGE, RESOLVED, extract_date, has_explicit_date, local_today,
                                       period_of, sync_date_fact)
from app.agent.llm import generate_with_tools
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending
from app.config import settings
from app.contracts.schemas import (
    ActiveWorkflow, BookingResult, InteractionKind, InteractionStatus, PendingInteraction, ServiceType,
    TaskRelationship, WorkflowStatus,
)
from app.identity import is_full_name, normalize_phone
from app.services import get_services

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Bạn là chuyên viên lịch hẹn của xưởng Foton (bảo dưỡng, sửa chữa, xem xe tại showroom, ký hợp đồng mua xe).
CÔNG CỤ: get_appointment_details (xem lịch của khách; gọi trước khi trả lời về lịch hẹn và trước khi đổi/hủy để lấy
appointment_id), create_appointment (đặt lịch mới), reschedule_appointment, cancel_appointment. Công cụ tự lấy dịch vụ,
ngày, giờ từ thông tin đã xác định; bạn không truyền ngày giờ.
QUY TẮC:
1. Tiếng Việt, ngắn gọn; chỉ dựa trên kết quả công cụ của lượt này. KHÔNG bịa giờ, trạng thái, mã lịch.
2. Kết quả status=NEEDS_CONFIRMATION: nêu đúng "proposal" và hỏi khách xác nhận; CHƯA được nói là đã làm. Đây là câu
   xác nhận DUY NHẤT: đủ thông tin thì gọi công cụ ngay, không tự hỏi "đúng không" trước.
   status=MISSING: ghi nhận ngắn thông tin khách vừa đưa rồi hỏi GỘP mọi mục trong "missing" trong một câu (kèm "message"
   nếu có). NEEDS_AM_PM: hỏi đúng một câu theo "message".
   ok=true: báo đã thực hiện theo "message". ok=false: báo chưa thực hiện được theo "message", gợi ý hotline {hotline};
   riêng SLOT_FULL/DUPLICATE: giữ nguyên số liệu của "message", gợi ý các giờ trong "alternatives" và hỏi khách chọn giờ
   (KHÔNG nói đã đặt, KHÔNG gợi ý hotline, KHÔNG tự chọn giờ).
3. "Khách vừa đồng ý" = có: gọi lại đúng công cụ của thao tác đang chờ xác nhận.
4. Nhiều lịch đang hiệu lực mà không rõ đổi/hủy lịch nào: liệt kê ngắn và hỏi. Yêu cầu đặt lịch MỚI -> create_appointment
   dù khách đã có lịch khác, không hỏi có muốn đổi lịch cũ.
5. Trạng thái dùng "status_label", thời gian dùng "when_label". Lịch bot đặt/đổi thành công là ĐÃ XÁC NHẬN ngay (không cần nhân viên duyệt); KHÔNG nói "chờ nhân viên xác nhận" trừ khi status_label là đang chờ.
6. Khách chỉ cập nhật tên/SĐT: ghi nhận, không gọi công cụ. Lịch ký hợp đồng: hợp đồng đã đủ thông tin, chỉ cần ngày giờ
   ký, KHÔNG hỏi lại thông tin hợp đồng.
7. Tra cứu hoặc sau khi thao tác thành công: trả lời đúng việc đó rồi dừng, không hỏi thêm (loại xe, biển số...) và không
   đề nghị việc khác. KHÔNG nhắc "proposal", mã lịch hay tên công cụ.
"""

AVAILABILITY_PROMPT = """\
Bạn là chuyên viên lịch hẹn của xưởng Foton. Khách đang HỎI khung giờ còn trống, chưa yêu cầu đặt lịch.
Gọi check_availability MỘT lần:
- date_text: đúng lời khách vừa nêu về ngày trong tin này (VD "thứ 7 này", "14/11", "ngày mai"); bỏ trống nếu khách không nêu
  ngày mới (hỏi tiếp về ngày đang bàn).
- period: morning nếu khách hỏi buổi sáng, afternoon nếu buổi chiều, còn lại all.
Trả lời dựa trên kết quả:
- Chỉ nêu các giờ trong "free", đúng như vậy, không thêm hay đổi giờ nào. KHÔNG bịa giờ.
- Khách nêu một giờ cụ thể (VD 9 giờ): nói rõ khung giờ đó còn trống hay đã đầy (có trong "free" hay không), rồi gợi ý các giờ còn lại.
- "free" rỗng: nói không còn khung trống và gợi ý buổi hoặc ngày khác.
- status=MISSING: hỏi đúng thông tin còn thiếu theo "message"/"missing".
Không nói là đã đặt lịch; để khách chọn giờ. Tiếng Việt, ngắn gọn.
"""

_ID = {"appointment_id": {"type": "integer", "description": "id lịch hẹn lấy từ get_appointment_details"}}
TOOLS = [
    {"name": "get_appointment_details", "description": "Xem các lịch hẹn của khách (mới nhất trước).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "check_availability",
     "description": "Xem các khung giờ còn trống trong ngày hẹn đã biết (không ghi gì). period: buổi khách hỏi.",
     "input_schema": {"type": "object", "required": [], "properties": {
         "period": {"type": "string", "enum": ["morning", "afternoon", "all"],
                    "description": "morning = sáng, afternoon = chiều, all = cả ngày"},
         "date_text": {"type": "string", "description": "Ngày khách vừa nêu trong tin này, nguyên văn; bỏ trống nếu không có"}}}},
    {"name": "create_appointment", "description": "Đặt lịch hẹn mới từ thông tin đặt lịch đã biết.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "reschedule_appointment", "description": "Đổi lịch hẹn sang ngày giờ mới đã biết.",
     "input_schema": {"type": "object", "required": ["appointment_id"], "properties": _ID}},
    {"name": "cancel_appointment", "description": "Hủy lịch hẹn.",
     "input_schema": {"type": "object", "required": ["appointment_id"], "properties": _ID}},
]
REQUIRED = {"service_type": "loại dịch vụ (bảo dưỡng/sửa chữa/xem xe/ký hợp đồng)", "appointment_when": "ngày giờ hẹn",
            "customer_name": "họ và tên đầy đủ", "customer_phone": "số điện thoại"}
LABELS = {ServiceType.MAINTENANCE.value: "bảo dưỡng", ServiceType.REPAIR.value: "sửa chữa",
          ServiceType.SHOWROOM_VISIT.value: "xem xe tại showroom", ServiceType.CONTRACT_SIGN.value: "ký hợp đồng mua xe"}
STATUS_LABELS = {"PENDING_APPROVAL": "đang chờ nhân viên xác nhận", "CONFIRMED": "đã được xác nhận",
                 "CANCELLED": "đã hủy", "REJECTED": "không được duyệt", "DONE": "đã hoàn thành"}
BOOKING_KEYS = {"service_type", "appointment_when", "appointment_date", "service_detail"}
DEAL_SERVICES = (ServiceType.SHOWROOM_VISIT.value, ServiceType.CONTRACT_SIGN.value)  # the car being sold is their subject
WEEKDAYS = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")
MAX_ASKS = 3  # turns asking the same thing (missing info / confirmation) before staff take over
FAILED = BookingResult(ok=False, status="ERROR", message="Hệ thống chưa thực hiện được thao tác này.")


def _booking_detail(state: AgentState, facts: dict) -> tuple[str | None, str | None]:
    """(vehicle, work item) for staff: the car an appointment task pointed at, else car_model; plus
    service_detail."""
    interpretation = state.get("conversation_interpretation")
    service, car = fact_value(facts, "service_type"), fact_value(facts, "car_model")
    pointed = next((t.car_model for t in (interpretation.tasks if interpretation else [])
                    if t.domain.value == "APPOINTMENT" and t.car_model), None)
    # The contract's car wins; a showroom visit falls back to it; a service visit is about the customer's own vehicle.
    vehicle = (car or pointed) if service == ServiceType.CONTRACT_SIGN.value else pointed or (
        car if service in DEAL_SERVICES else None)
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


def _availability_only(state: AgentState) -> bool:
    """Every task handled here only asks which hours are free: the agent gets just the read-only lookup, so it cannot
    re-run the booking check and repeat an old "slot full" answer."""
    interpretation = state.get("conversation_interpretation")
    mine = set(state.get("task_ids") or [])
    tasks = [t for t in (interpretation.tasks if interpretation else []) if not mine or t.task_id in mine]
    return bool(tasks) and all(t.action == "check_availability" for t in tasks)


def _new_time_this_turn(state: AgentState) -> bool:
    interpretation = state.get("conversation_interpretation")
    return bool(interpretation and any(u.key == "appointment_when" for u in interpretation.slot_updates))


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


def _slot_problem(booking, session_id: str, service_type: str, day: str, at: str | None, phone: str | None = None,
                  exclude_id: int | None = None) -> dict | None:
    """Khung giờ đã đủ chỗ hoặc khách đã có lịch cùng khung giờ -> kết quả ok=False (kèm giờ còn trống) để báo khách TRƯỚC khi đề
    xuất. Lỗi kiểm tra không chặn đặt lịch: BookingService kiểm tra lại lúc ghi."""
    try:
        result = booking.check_slot(session_id, service_type, date.fromisoformat(day),
                                    time.fromisoformat(at) if at else None, exclude_id=exclude_id, phone=phone)
    except Exception:
        logger.exception("Slot check failed")
        return None
    return None if result.ok else result.model_dump()


def appointment(state: AgentState) -> dict:
    facts = dict(state.get("known_facts") or {})
    session_id = state.get("session_id", "")
    booking = get_services().booking
    workflow = state.get("active_workflow")
    mine = open_pending(state, "appointment")
    confirmation = next((item for item in mine if item.kind == InteractionKind.CONFIRMATION
                         and item.metadata.get("turn") == _turn(state) - 2), None)  # a stale one is never accepted
    approved = confirmation.metadata if confirmation and _accepted(state) else None
    vehicle, detail = _booking_detail(state, facts)
    # Date and hour both come from the customer's words (date_extraction): the LLM only reads components, the code
    # computes. The customer gives the exact hour too, so staff do not have to call back for it.
    resolved = sync_date_fact(facts) or {}
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

    def when() -> tuple[str, str] | dict:
        day_ok = resolved.get("status") == RESOLVED
        if resolved.get("time_status") == NEEDS_PERIOD:
            return {"status": "NEEDS_AM_PM", "message": "Khách nói giờ mà chưa nói là buổi sáng hay chiều/tối."}
        time_ok = resolved.get("time_status") == RESOLVED
        if gaps := ([] if day_ok else ["ngày hẹn cụ thể"]) + ([] if time_ok else ["giờ hẹn cụ thể"]):
            missing[:] = gaps
            reason = ("ngày khách nói nằm ngoài khoảng nhận lịch (từ hôm nay đến hết tháng sau)"
                      if resolved.get("status") == OUT_OF_RANGE else
                      "chưa xác định được ngày cụ thể từ lời khách (VD: thứ 7 tuần này, ngày 15/12)" if not day_ok
                      else "khách chưa nêu giờ cụ thể (VD: 9 giờ sáng, 14h30)")
            return {"status": "MISSING", "missing": missing, "message": reason}
        return resolved["date"], resolved["time"]

    def availability(tool_args: dict) -> dict:
        # The day comes from the customer's words: the LLM passes them, the code computes the date (as for bookings).
        # A date_text without a day ("buổi chiều", "chiều") is ignored: the day stays the one already discussed.
        date_text = str(tool_args.get("date_text") or "")
        day = extract_date(date_text, local_today()) if has_explicit_date(date_text) else resolved
        if day.get("status") != RESOLVED:
            missing[:] = ["ngày hẹn cụ thể"]
            return {"status": "MISSING", "missing": missing, "message": (
                "ngày khách nói nằm ngoài khoảng nhận lịch (từ hôm nay đến hết tháng sau)"
                if day.get("status") == OUT_OF_RANGE else "chưa xác định được ngày cụ thể cần xem giờ trống")}
        service = fact_value(facts, "service_type") or ServiceType.MAINTENANCE.value  # bay slots unless a deal visit
        said = period_of(f"{state.get('task_text') or ''} {state.get('user_text') or ''}")  # the customer's words win
        period = said or (tool_args.get("period") if tool_args.get("period") in ("morning", "afternoon") else None)
        info = booking.available_windows(session_id, str(service), date.fromisoformat(day["date"]), period)
        label = {"morning": "buổi sáng", "afternoon": "buổi chiều"}.get(period, "cả ngày")
        free = info["free"]
        message = (f"{_fmt(info['date'], None)}, {label} còn trống các khung giờ (mỗi khung {info['slot_minutes']} phút): "
                   + ", ".join(free) + "." if free else
                   f"{_fmt(info['date'], None)}, {label} không còn khung giờ trống.")
        return {"ok": True, "status": "AVAILABILITY", "free": free, "message": message}

    def run_args(a: dict) -> tuple[date, time | None]:
        return date.fromisoformat(a["date"]), time.fromisoformat(a["time"]) if a["time"] else None

    def create() -> dict:
        phone = normalize_phone(str(fact_value(facts, "customer_phone") or ""))
        name = str(fact_value(facts, "customer_name") or "")
        present = {"customer_phone": phone, "customer_name": is_full_name(name)}  # a given name alone is not enough
        missing[:] = [label for key, label in REQUIRED.items() if not present.get(key, fact_value(facts, key))]
        if missing:
            invalid = "customer_phone" in (state.get("rejected_slots") or [])
            return {"status": "MISSING", "missing": missing,
                    **({"invalid": "số điện thoại chưa đúng định dạng (10 số, bắt đầu bằng 0)"} if invalid else {})}
        if isinstance(deduced := when(), dict):
            return deduced
        args = {"service_type": str(fact_value(facts, "service_type")), "when": str(fact_value(facts, "appointment_when")),
                "date": deduced[0], "time": deduced[1], "name": name, "phone": phone, "vehicle": vehicle,
                "detail": detail}
        if blocked := _slot_problem(booking, session_id, args["service_type"], deduced[0], deduced[1], phone):
            return blocked
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
        if isinstance(deduced := when(), dict):
            return deduced
        args = {"appointment_id": appointment_id, "date": deduced[0], "time": deduced[1]}
        if blocked := _slot_problem(booking, session_id, info["service_type"], deduced[0], deduced[1],
                                    exclude_id=appointment_id):
            return blocked
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
            elif name == "check_availability":
                result = availability(tool_args)
            elif name == "create_appointment":
                result = create()
            elif name in ("reschedule_appointment", "cancel_appointment"):
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
    today = local_today()
    booked_day = (_fmt(resolved["date"], None) if resolved.get("status") == RESOLVED
                  else "chưa xác định, cần hỏi khách ngày cụ thể" if resolved else "khách chưa nêu")
    context = (f"Hôm nay: {WEEKDAYS[today.weekday()]} {today:%d/%m/%Y}\nNgày hẹn đã xác định: {booked_day}\n"
               f"Tin nhắn của khách: {state.get('user_text', '')}\n"
               f"Yêu cầu: {state.get('task_text') or state.get('user_text', '')}\n"
               f"Thông tin đặt lịch đã biết: {json.dumps(known, ensure_ascii=False)}\n"
               f"Xác nhận đang chờ: {waiting_for}\nKhách vừa đồng ý xác nhận: {'có' if approved else 'không'}")
    only_availability = _availability_only(state)
    system = (AVAILABILITY_PROMPT if only_availability else SYSTEM_PROMPT.format(hotline=settings.hotline)) + STYLE_RULES
    tools = [t for t in TOOLS if (t["name"] == "check_availability") == only_availability]
    out = generate_with_tools(system, context, tools, execute, history=state.get("history"), max_turns=4)
    reply = (out.get("final_response") or "").strip() or _fallback(results, proposal, missing)
    return _update(state, facts, workflow, mine, reply, results, done, proposal, missing)


def _fallback(results: list[tuple[str, dict]], proposal: dict, missing: list[str]) -> str:
    """Reply from the tool results when the LLM gave no text."""
    if proposal:
        return f"Dạ bạn xác nhận giúp mình: bạn muốn {proposal['text']} phải không ạ?"
    if missing:
        return f"Dạ bạn cho mình xin {missing[0]} nhé."
    last = results[-1][1] if results else {}
    message = last.get("message", "")
    if last.get("status") in ("SLOT_FULL", "DUPLICATE"):
        return f"{message} Bạn chọn giúp mình một giờ khác nhé.".strip()
    if message:
        return message if last.get("ok") else f"{message} Bạn có thể gọi hotline {settings.hotline}."
    return "Dạ mình chưa tìm thấy lịch hẹn nào của bạn ạ."


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
    looked_up = any((name == "get_appointment_details" and result.get("appointments"))
                    or (name == "check_availability" and result.get("status") == "AVAILABILITY")
                    for name, result in results)
    update: dict = {"trace": ["appointment"], "known_facts": facts,
                    "answers": [answer(state, "appointment", reply, executed=bool(succeeded) or looked_up)]}

    if proposal or missing:
        kind = InteractionKind.CONFIRMATION if proposal else InteractionKind.QUESTION
        # Same question as last turn again: count it; after MAX_ASKS staff take over instead of asking forever.
        asks = 1 + max((int(item.metadata.get("asks", 1)) for item in mine if item.kind == kind
                        and item.metadata.get("missing_slots") == (None if proposal else missing)
                        and item.metadata.get("action") == (proposal.get("action") if proposal else None)), default=0)
        if asks > MAX_ASKS:
            update["answers"] = [answer(state, "appointment", needs_human=True,
                                        handover_reason="APPOINTMENT_APPROVAL: booking not settled after repeated asks")]
            update["pending_interactions"] = _pending(mine, InteractionStatus.EXPIRED)
            update["active_workflow"] = workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL})
            return update
        metadata = ({"workflow": "appointment", "action": proposal["action"], "args": proposal["args"],
                     "turn": _turn(state), "asks": asks} if proposal
                    else {"workflow": "appointment", "missing_slots": missing, "asks": asks})
        new = PendingInteraction(interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=kind, text=reply,
                                 status=InteractionStatus.OPEN, metadata=metadata)
        update["pending_interactions"] = _pending(mine, InteractionStatus.EXPIRED, new)
        update["active_workflow"] = workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER})
    elif succeeded:
        update["pending_interactions"] = _pending(mine, InteractionStatus.RESOLVED)
        if "create_appointment" in succeeded:
            # The finished task's leftovers must not drive the next one: the stored request would book twice, and
            # the showroom/contract car and agreed price belong to that deal. car_list stays (the customer may
            # still pick from it); a MAINTENANCE/REPAIR car is the customer's own vehicle, so it stays too.
            deal = fact_value(facts, "service_type") in DEAL_SERVICES
            for key in BOOKING_KEYS | ({"car_model", "decided_price"} if deal else set()):
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
