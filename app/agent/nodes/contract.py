"""Contract Agent: collect purchase-contract fields (name, phone, address, car model, agreed price).
Fields come from known_facts (validated by Preprocessing). Any change to deal terms goes to a human."""
import json
import uuid

from app.agent.llm import generate
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value, open_pending
from app.contracts.schemas import ActiveWorkflow, InteractionKind, InteractionStatus, PendingInteraction, WorkflowStatus

REQUIRED_FIELDS = {
    "customer_name": "Họ và tên",
    "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ nhận xe / hộ khẩu",
    "car_model": "Dòng xe chọn mua",
    "decided_price": "Mức giá chốt hợp đồng (VNĐ)",
}

CONTRACT_SYSTEM_PROMPT = """\
Bạn là chuyên viên pháp lý và thủ tục hợp đồng mua xe Foton.
QUY TẮC:
1. PHẢI xác nhận/ghi nhận thông tin khách vừa cung cấp trước khi hỏi tiếp.
2. Nếu còn thông tin thiếu, chúc mừng khách đã chốt xe và nhẹ nhàng xin nốt các thông tin còn thiếu.
3. Nếu đã ĐỦ TẤT CẢ thông tin, tóm tắt lại toàn bộ hợp đồng (Tên, SĐT, Địa chỉ, Dòng xe, Giá chốt)
   và hẹn ngày ký kết/bàn giao xe.
4. Luôn trả lời bằng tiếng Việt, lịch sự và chuyên nghiệp.
5. KHÔNG tự bịa thông tin khách chưa cung cấp.
""" + STYLE_RULES
SUMMARY_CLOSING = ("Tư vấn viên bên shop sẽ liên hệ bạn để hẹn ngày ký kết và bàn giao xe. "
                   "Cảm ơn bạn đã tin tưởng và sử dụng dịch vụ bên mình!")


def contract(state: AgentState) -> dict:
    facts = state.get("known_facts") or {}
    interpretation = state.get("conversation_interpretation")
    collected = {key: fact_value(facts, key) for key in REQUIRED_FIELDS if fact_value(facts, key) is not None}
    present = {REQUIRED_FIELDS[key]: value for key, value in collected.items()}
    missing = [key for key in REQUIRED_FIELDS if key not in collected]
    workflow = state.get("active_workflow")
    if not workflow or workflow.workflow_type != "contract":
        workflow = ActiveWorkflow(workflow_id=f"{state.get('session_id', 'session')}_contract",
                                  workflow_type="contract", status=WorkflowStatus.ACTIVE)
    pending = open_pending(state, "contract")

    # Contract/price changes need a human. Preprocessing's LLM judges this in context (a keyword list
    # wrongly escalated "Giá chốt là 480 triệu" and names such as "Phí Văn Long").
    if interpretation and interpretation.signals.deal_change:
        return {"answers": [answer(state, "contract", needs_human=True, handover_reason="CONTRACT_OR_PRICE_CHANGE")],
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL}),
                "trace": ["contract"]}

    message = state.get("task_text") or state.get("user_text", "")
    if missing:
        labels = [REQUIRED_FIELDS[key] for key in missing]
        reply = generate(CONTRACT_SYSTEM_PROMPT,
                         f"Câu hỏi/tin nhắn của khách: {message}\n\n"
                         f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(present, ensure_ascii=False)}\n\n"
                         f"⚠️ THÔNG TIN CÒN THIẾU: {labels}\n\n"
                         "Hãy chúc mừng khách và xin nốt các thông tin còn thiếu.",
                         history=state.get("history")) or (
            f"🎉 Chúc mừng bạn đã quyết định chốt xe!\nĐể hoàn tất hợp đồng, mình cần thêm: {', '.join(labels)}.")
        question = PendingInteraction(
            interaction_id=f"contract_{uuid.uuid4().hex[:8]}", kind=InteractionKind.QUESTION, text=reply,
            status=InteractionStatus.OPEN, metadata={"workflow": "contract", "missing_fields": missing})
        expired = [item.model_copy(update={"status": InteractionStatus.EXPIRED}) for item in pending]
        return {"answers": [answer(state, "contract", reply)],
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_USER,
                                                               "collected_facts": collected}),
                "pending_interactions": [*expired, question], "trace": ["contract"]}

    reply = generate(CONTRACT_SYSTEM_PROMPT,
                     f"Câu hỏi/tin nhắn của khách: {message}\n\n"
                     f"📋 THÔNG TIN ĐẦY ĐỦ: {json.dumps(present, ensure_ascii=False)}\n\n"
                     "Đã đủ tất cả thông tin. Hãy tóm tắt hợp đồng và hẹn ngày ký kết.",
                     history=state.get("history")) or (
        "📄 TÓM TẮT HỢP ĐỒNG:\n" + "\n".join(f"  • {label}: {value}" for label, value in present.items())
        + f"\n\n{SUMMARY_CLOSING}")
    return {"answers": [answer(state, "contract", reply, executed=True)],
            "pending_interactions": [item.model_copy(update={"status": InteractionStatus.RESOLVED}) for item in pending],
            "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.NONE}), "trace": ["contract"]}
