"""Contract Agent: collect purchase-contract fields (name, phone, address, car model, agreed price).
Fields come from known_facts (validated by Preprocessing). Any change to deal terms goes to a human.

The car model may come from the product the customer picked ("mẫu thứ 2", resolved by Preprocessing against
the products the bot showed) instead of a typed name. Asking stops: after MAX_ASKS turns without a new field the
contract goes to staff. Once complete, the signing date is booked by the appointment agent (CONTRACT_SIGN, with
the car model in the appointment note), so the contract agent never asks for a date itself."""
import json
import uuid

from app.agent.llm import generate
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
1. PHẢI xác nhận/ghi nhận thông tin khách vừa cung cấp trước khi hỏi tiếp.
2. Nếu còn thông tin thiếu, xin đúng các thông tin trong danh sách CÒN THIẾU; KHÔNG hỏi lại hay xin khách xác
   nhận lại thông tin đã có trong THÔNG TIN ĐÃ CÓ.
3. Nếu đã ĐỦ TẤT CẢ thông tin, tóm tắt lại hợp đồng (Tên, SĐT, Địa chỉ, Dòng xe, Giá chốt) rồi hỏi khách
   muốn hẹn ngày giờ nào đến ký hợp đồng. KHÔNG tự đề xuất hay tự chọn ngày giờ.
4. Luôn trả lời bằng tiếng Việt, lịch sự và chuyên nghiệp.
5. KHÔNG tự bịa thông tin khách chưa cung cấp.
""" + STYLE_RULES


def _chosen_vehicle(state: AgentState) -> str | None:
    """The product the customer picked: one referenced this turn, else the one sales last focused on."""
    interpretation = state.get("conversation_interpretation")
    shown = {item.get("entity_id"): item.get("name") for item in state.get("selected_entities") or []
             if isinstance(item, dict) and item.get("entity_type") == "PRODUCT"}
    referenced = {shown[t.entity_id] for t in (interpretation.tasks if interpretation else []) if shown.get(t.entity_id)}
    if len(referenced) == 1:
        return referenced.pop()
    return (state.get("focused_product") or {}).get("name")


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
        asks = _asks(pending, missing)
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

    reply = generate(CONTRACT_SYSTEM_PROMPT,
                     f"Câu hỏi/tin nhắn của khách: {message}\n\n"
                     f"📋 THÔNG TIN ĐẦY ĐỦ: {json.dumps(present, ensure_ascii=False)}\n\n"
                     "Đã đủ tất cả thông tin. Hãy tóm tắt hợp đồng và hỏi khách muốn hẹn ngày giờ nào đến ký.",
                     history=state.get("history")) or (
        "Tóm tắt hợp đồng:\n" + "\n".join(f"- {label}: {value}" for label, value in present.items())
        + "\n\nBạn muốn hẹn ngày giờ nào đến ký hợp đồng ạ?")
    # The signing date is the appointment agent's job: CONTRACT_SIGN, vehicle from car_model.
    facts["service_type"] = KnownFact(key="service_type", value=ServiceType.CONTRACT_SIGN.value,
                                      source=FactSource.APPLICATION_VERIFIED)
    facts.pop("appointment_when", None)  # an older booking time is not the signing date
    offer = PendingInteraction(
        interaction_id=f"appointment_{uuid.uuid4().hex[:8]}", kind=InteractionKind.QUESTION, text=reply,
        status=InteractionStatus.OPEN, metadata={"workflow": "appointment", "missing_slots": ["ngày giờ ký hợp đồng"]})
    signing = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_appointment",
                             workflow_type="appointment", status=WorkflowStatus.WAITING_FOR_USER,
                             collected_facts={"contract": collected})
    return {"answers": [answer(state, "contract", reply, executed=True)],
            "pending_interactions": [*(item.model_copy(update={"status": InteractionStatus.RESOLVED})
                                       for item in pending), offer],
            "active_workflow": signing, "known_facts": facts, "trace": ["contract"]}
