"""[TRACK C] Contract Agent: slot-filling hợp đồng mua xe.
Port từ codebase hiện tại: thu thập name, phone, address, car_model, decided_price.
Chỉ gửi template: mọi yêu cầu thay đổi hợp đồng hoặc hỏi về giá -> chuyển người."""
import json
import uuid

from app.agent.llm import generate
from app.agent.nodes.common import STYLE_RULES, answer, step
from app.agent.state import AgentState
from app.contracts.schemas import (
    ActiveWorkflow,
    FactSource,
    InteractionKind,
    InteractionStatus,
    KnownFact,
    PendingInteraction,
    WorkflowStatus,
)


# Các trường cần thiết để hoàn tất hợp đồng
REQUIRED_FIELDS = {
    "customer_name": "Họ và tên",
    "customer_phone": "Số điện thoại",
    "customer_address": "Địa chỉ nhận xe / hộ khẩu",
    "car_model": "Dòng xe chọn mua",
    "decided_price": "Mức giá chốt hợp đồng (VNĐ)",
}

ESCALATE = ["sửa hợp đồng", "thay đổi", "đổi điều khoản", "điều khoản", "giá", "bao nhiêu", "chi phí", "phí"]

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


def _value(facts: dict, key: str):
    fact = facts.get(key)
    return fact.value if isinstance(fact, KnownFact) else fact.get("value") if isinstance(fact, dict) else None


def _open_contract_pending(state: AgentState) -> list[PendingInteraction]:
    pending = []
    for item in state.get("pending_interactions", []) or []:
        item = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if item.status == InteractionStatus.OPEN and item.metadata.get("workflow") == "contract":
            pending.append(item)
    return pending


def _replace_contract_pending(state: AgentState, interaction: PendingInteraction | None) -> list[PendingInteraction]:
    result = []
    for item in state.get("pending_interactions", []) or []:
        pending = item if isinstance(item, PendingInteraction) else PendingInteraction.model_validate(item)
        if pending.status == InteractionStatus.OPEN and pending.metadata.get("workflow") == "contract":
            pending = pending.model_copy(update={"status": InteractionStatus.EXPIRED})
        result.append(pending)
    if interaction:
        result.append(interaction)
    return result


def contract(state: AgentState) -> dict:
    facts = dict(state.get("known_facts", {}) or {})
    interpretation = state.get("conversation_interpretation")
    text = state.get("task_text") or state.get("user_text", "")
    for update in interpretation.slot_updates if interpretation else []:
        key = update.key
        if key not in REQUIRED_FIELDS:
            continue
        if update.operation.value == "CLEAR":
            facts.pop(key, None)
        else:
            previous = facts.get(key)
            facts[key] = KnownFact(
                key=key,
                value=update.value,
                source=FactSource.USER_CORRECTED if update.operation.value == "CORRECT" or (
                    previous is not None and _value(facts, key) != update.value
                ) else FactSource.USER_EXPLICIT,
                updated_at_message_id=update.source_message_id,
            )
    present_fields = {label: _value(facts, key) for key, label in REQUIRED_FIELDS.items()
                      if _value(facts, key) is not None}
    missing_keys = [key for key in REQUIRED_FIELDS if _value(facts, key) is None]
    missing_fields = [REQUIRED_FIELDS[key] for key in missing_keys]
    workflow = state.get("active_workflow")
    # Checkpoint may deserialize as dict; reconstruct if needed
    if isinstance(workflow, dict):
        workflow = ActiveWorkflow(**workflow)
    if not workflow or workflow.workflow_type != "contract":
        workflow = ActiveWorkflow(
            workflow_id=f"{state.get('session_id', 'session')}_contract",
            workflow_type="contract", status=WorkflowStatus.ACTIVE,
            collected_facts=present_fields,
        )
    if any(k in text.lower() for k in ESCALATE):
        return {"answers": [answer(state, "contract", needs_human=True,
                                   handover_reason="CONTRACT_OR_PRICE_CHANGE")],
                "known_facts": facts,
                "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.WAITING_FOR_APPROVAL}),
                "trace": step(state, "contract")}

    if missing_fields:
        # Dùng LLM để hỏi thông tin thiếu một cách tự nhiên
        llm_reply = generate(
            CONTRACT_SYSTEM_PROMPT,
            f"Câu hỏi/tin nhắn của khách: {state.get("task_text") or state.get("user_text", "")}\n\n"
            f"📋 THÔNG TIN ĐÃ CÓ: {json.dumps(present_fields, ensure_ascii=False)}\n\n"
            f"⚠️ THÔNG TIN CÒN THIẾU: {missing_fields}\n\n"
            f"Hãy chúc mừng khách và xin nốt các thông tin còn thiếu.",
            history=state.get("history"),
        )
        if llm_reply:
            reply = llm_reply
        else:
            # Fallback
            missing_str = ", ".join(missing_fields)
            reply = f"🎉 Chúc mừng bạn đã quyết định chốt xe!\nĐể hoàn tất hợp đồng, mình cần thêm: {missing_str}."

        interaction_id = f"contract_{uuid.uuid4().hex[:8]}"
        pending_interaction = PendingInteraction(
            interaction_id=interaction_id,
            kind=InteractionKind.QUESTION,
            text=reply,
            status=InteractionStatus.OPEN,
            related_task_id=None,
            created_at_message_id=None,
            metadata={"workflow": "contract", "missing_fields": missing_keys},
        )
        pending_interactions = _replace_contract_pending(state, pending_interaction)
        workflow = workflow.model_copy(update={
            "status": WorkflowStatus.WAITING_FOR_USER,
            "collected_facts": {key: _value(facts, key) for key in REQUIRED_FIELDS if _value(facts, key) is not None},
        })

        return {"answers": [answer(state, "contract", reply)],
                "known_facts": facts,
                "active_workflow": workflow,
                "pending_interactions": pending_interactions,
                "trace": step(state, "contract")}
    else:
        # Đã đủ thông tin → tóm tắt hợp đồng
        llm_reply = generate(
            CONTRACT_SYSTEM_PROMPT,
            f"Câu hỏi/tin nhắn của khách: {state.get("task_text") or state.get("user_text", "")}\n\n"
            f"📋 THÔNG TIN ĐẦY ĐỦ: {json.dumps(present_fields, ensure_ascii=False)}\n\n"
            f"Đã đủ tất cả thông tin. Hãy tóm tắt hợp đồng và hẹn ngày ký kết.",
            history=state.get("history"),
        )
        if llm_reply:
            reply = llm_reply
        else:
            info_str = "\n".join(f"  • {label}: {value}" for label, value in present_fields.items())
            polite_response = "Tư vấn viên bên em sẽ liên hệ anh/chị để hẹn ngày ký kết và bàn giao xe. Cảm ơn anh/chị đã tin tưởng và sử dụng dịch vụ bên em!"
            reply = f"📄 TÓM TẮT HỢP ĐỒNG:\n{info_str}\n\n{polite_response}"

    pending_interactions = [item.model_copy(update={"status": InteractionStatus.RESOLVED})
                           for item in _open_contract_pending(state)]
    return {"answers": [answer(state, "contract", reply)], "known_facts": facts,
            "pending_interactions": pending_interactions,
            "active_workflow": workflow.model_copy(update={"status": WorkflowStatus.NONE}),
            "trace": step(state, "contract")}
