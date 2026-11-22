"""Contract Agent: collect purchase-contract fields (name, phone, address, car model, agreed price).
Fields come from known_facts (validated by Preprocessing). Any change to deal terms goes to a human.

The car model may come from the product the customer picked ("mẫu thứ 2", resolved by Preprocessing against
the products the bot showed) instead of a typed name. Asking stops: after MAX_ASKS turns without a new field the
contract goes to staff. Once complete, the signing date is booked by the appointment agent (CONTRACT_SIGN, with
the car model in the appointment note), so the contract agent never asks for a date itself."""
import json
import uuid
from datetime import date

from app.agent.date_extraction import RESOLVED, sync_date_fact
from app.agent.llm import generate
from app.agent.nodes.appointment import appointment
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending
from app.identity import is_full_name
from app.contracts.schemas import (
    ActiveWorkflow, FactSource, InteractionKind, InteractionStatus, KnownFact, PendingInteraction, ServiceType,
    WorkflowStatus,
)


REQUIRED_FIELDS = {
    "customer_name": "Họ và tên đầy đủ",
    "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ nhận xe / hộ khẩu",
    "car_model": "Dòng xe chọn mua",
    "decided_price": "Mức giá chốt hợp đồng (VNĐ)",
}
MAX_ASKS = 3  # turns asking for the same missing fields before staff take over

CONTRACT_SYSTEM_PROMPT = """\
Bạn là chuyên viên pháp lý và thủ tục hợp đồng mua xe Foton.
QUY TẮC:
1. Ghi nhận ngắn (một câu) thông tin khách vừa cung cấp; KHÔNG liệt kê lại toàn bộ thông tin đã có.
2. Nếu còn thông tin thiếu, xin GỘP mọi thông tin trong danh sách CÒN THIẾU trong một câu; KHÔNG hỏi lại hay xin
   khách xác nhận lại thông tin đã có trong THÔNG TIN ĐÃ CÓ, KHÔNG hỏi thêm điều ngoài danh sách đó.
3. Nếu đã ĐỦ TẤT CẢ thông tin, tóm tắt lại hợp đồng (Tên, SĐT, Địa chỉ, Dòng xe, Giá chốt) rồi hỏi khách
   muốn hẹn ngày giờ nào đến ký hợp đồng. KHÔNG tự đề xuất hay tự chọn ngày giờ.
4. Luôn trả lời bằng tiếng Việt, lịch sự và chuyên nghiệp.
5. KHÔNG tự bịa thông tin khách chưa cung cấp.
""" + STYLE_RULES


def _chosen_vehicle(state: AgentState) -> str | None:
    """The car the customer picked this turn, as the preprocessing resolved it from the list they count."""
    interpretation = state.get("conversation_interpretation")
    referenced = {t.car_model for t in (interpretation.tasks if interpretation else []) if t.car_model}
    return referenced.pop() if len(referenced) == 1 else None


def _asks(pending: list[PendingInteraction], missing: list[str]) -> int:
    """Consecutive turns asking for the same missing fields, this one included."""
    last = next((item for item in reversed(pending) if item.kind == InteractionKind.QUESTION), None)
    if last and set(missing) >= set(last.metadata.get("missing_fields") or []):
        return int(last.metadata.get("asks", 1)) + 1
    return 1


def contract(state: AgentState) -> dict:
    facts = dict(state.get("known_facts") or {})
    interpretation = state.get("conversation_interpretation")
    if not fact_value(facts, "car_model") and (vehicle := _chosen_vehicle(state)):
        facts["car_model"] = KnownFact(key="car_model", value=vehicle, source=FactSource.INFERRED)
    collected = {key: fact_value(facts, key) for key in REQUIRED_FIELDS if fact_value(facts, key) is not None}
    if not is_full_name(str(collected.get("customer_name") or "")):
        collected.pop("customer_name", None)  # hợp đồng cần họ tên đầy đủ, không chỉ tên gọi
    present = {REQUIRED_FIELDS[key]: value for key, value in collected.items()}
    missing = [key for key in REQUIRED_FIELDS if key not in collected]
    workflow = state.get("active_workflow")
    if not workflow or workflow.workflow_type != "contract":
        workflow = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_contract",
                                  workflow_type="contract", status=WorkflowStatus.ACTIVE)
    pending = open_pending(state, "contract")
    expired = [item.model_copy(update={"status": InteractionStatus.EXPIRED}) for item in pending]

    # Contract/price changes need a human. Preprocessing's LLM judges this in context (a keyword list
    # wrongly escalated "Giá chốt là 480 triệu" and names such as "Phí Văn Long").
    if interpretation and interpretation.signals.deal_change:
        return {"answers": [answer(state, "contract", needs_human=True, handover_reason="CONTRACT_OR_PRICE_CHANGE")],
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL}),
                "known_facts": facts, "trace": ["contract"]}

    message = state.get("task_text") or state.get("user_text", "")
    if missing:
        # A message that gave the bot new information is progress, not the same question asked again.
        asks = 1 if interpretation and interpretation.slot_updates else _asks(pending, missing)
        if asks > MAX_ASKS:  # the same question again would not help: staff finish the contract
            return {"answers": [answer(state, "contract", needs_human=True,
                                       handover_reason="OTHER: contract fields still missing after repeated asks")],
                    "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL,
                                                                   "collected_facts": collected}),
                    "pending_interactions": expired, "known_facts": facts, "trace": ["contract"]}
        labels = [REQUIRED_FIELDS[key] for key in missing]
        reply = generate(CONTRACT_SYSTEM_PROMPT,
                         f"Câu hỏi/tin nhắn của khách: {message}\n\n"
                         f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(present, ensure_ascii=False)}\n\n"
                         f"⚠️ THÔNG TIN CÒN THIẾU: {labels}\n\n"
                         "Hãy xin nốt các thông tin còn thiếu.",
                         history=state.get("history")) or (
            f"Để hoàn tất hợp đồng, mình cần thêm: {', '.join(labels)}.")
        question = PendingInteraction(
            interaction_id=f"contract_{uuid.uuid4().hex[:8]}", kind=InteractionKind.QUESTION, text=reply,
            status=InteractionStatus.OPEN,
            metadata={"workflow": "contract", "missing_fields": missing, "asks": asks})
        return {"answers": [answer(state, "contract", reply)],
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER,
                                                               "collected_facts": collected}),
                "pending_interactions": [*expired, question], "known_facts": facts, "trace": ["contract"]}

    # A signing time given in this message ("giá chốt 650 triệu, sáng thứ 6 tôi qua ký") is kept and booked now;
    # an older booking time is not the signing date.
    signing_time = next((u.value for u in (interpretation.slot_updates if interpretation else [])
                         if u.key == "appointment_when" and u.value), None)
    if not signing_time:
        facts.pop("appointment_when", None)
    signing_day = sync_date_fact(facts) or {}
    if signing_day.get("status") == RESOLVED:
        signing_time = f"{signing_time} (ngày {date.fromisoformat(signing_day['date']):%d/%m/%Y})"
    ask = (f"Khách đã nêu thời gian ký: {signing_time}. Tóm tắt hợp đồng ngắn gọn; KHÔNG hỏi ngày giờ ký, KHÔNG hỏi "
           "xác nhận (bước xác nhận lịch ký nằm ở phần sau)." if signing_time else
           "Đã đủ tất cả thông tin. Hãy tóm tắt hợp đồng và hỏi khách muốn hẹn ngày giờ nào đến ký.")
    reply = generate(CONTRACT_SYSTEM_PROMPT,
                     f"Câu hỏi/tin nhắn của khách: {message}\n\n"
                     f"📋 THÔNG TIN ĐẦY ĐỦ: {json.dumps(present, ensure_ascii=False)}\n\n{ask}",
                     history=state.get("history")) or (
        "Tóm tắt hợp đồng:\n" + "\n".join(f"- {label}: {value}" for label, value in present.items())
        + ("" if signing_time else "\n\nBạn muốn hẹn ngày giờ nào đến ký hợp đồng ạ?"))
    # The signing date is the appointment agent's job: CONTRACT_SIGN, vehicle from car_model.
    facts["service_type"] = KnownFact(key="service_type", value=ServiceType.CONTRACT_SIGN.value,
                                      source=FactSource.APPLICATION_VERIFIED)
    offer = PendingInteraction(
        interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=InteractionKind.QUESTION, text=reply,
        status=InteractionStatus.OPEN, metadata={"workflow": "appointment", "missing_slots": ["ngày giờ ký hợp đồng"]})
    signing = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_appointment",
                             workflow_type="appointment", status=WorkflowStatus.WAITING_FOR_USER,
                             collected_facts={"contract": collected})
    resolved = [item.model_copy(update={"status": InteractionStatus.RESOLVED}) for item in pending]
    summary = answer(state, "contract", reply, executed=True)
    if signing_time:
        # Same turn: the appointment agent proposes the signing slot, so the customer confirms once.
        booked = appointment({**state, "known_facts": facts, "active_workflow": signing,
                              "pending_interactions": open_pending(state, "appointment")})
        return {"answers": [summary, *booked["answers"]],
                "pending_interactions": [*resolved, *booked.get("pending_interactions", [])],
                "active_workflow": booked.get("active_workflow", signing), "known_facts": booked["known_facts"],
                "trace": ["contract", "appointment"]}
    return {"answers": [summary], "pending_interactions": [*resolved, offer],
            "active_workflow": signing, "known_facts": facts, "trace": ["contract"]}
