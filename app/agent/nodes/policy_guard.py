"""Policy and Safety Guard: LLM agent with policy tools, plus hard business rules in code.

Hard rules (never delegated to the LLM):
- A discount/bargaining request always goes to a human manager for approval.
Judgement calls (human request, complaint, safety, complexity) are made by an LLM agent
that reads the structured interpretation and acts only through tools. The agent runs only
when preprocessing raised a policy signal or HIGH complexity, so ordinary turns add no
latency. Without an LLM the preprocessing signals are applied directly.
"""
import json

from app.agent.llm import generate_with_tools
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.contracts.schemas import Intent
from app.services import get_services

HANDOVER_REASONS = ["HUMAN_REQUEST", "COMPLAINT", "COMPLEXITY_HIGH", "SAFETY", "OTHER"]

POLICY_PROMPT = """\
Bạn là Policy and Safety Guard của trợ lý gara ô tô. Bạn nhận tin nhắn của khách và kết quả
phân tích (tasks, signals). Quyết định bằng cách gọi tool, KHÔNG trả lời khách.
- Khách yêu cầu rõ gặp nhân viên/người thật -> handover_to_human(HUMAN_REQUEST).
- Khách khiếu nại, bức xúc về dịch vụ/sản phẩm -> handover_to_human(COMPLAINT). Một câu chê nhẹ
  mà bot vẫn trả lời được (VD "giá hơi cao") không phải khiếu nại.
- Xe có dấu hiệu nguy hiểm khi vận hành (mất phanh, cháy, khói, mất lái, đèn áp suất dầu) ->
  flag_safety_risk; thêm handover_to_human(SAFETY) nếu khách đang gặp nguy hiểm ngay lúc này.
- Câu hỏi kỹ thuật cần chẩn đoán chuyên sâu vượt quá tài liệu -> handover_to_human(COMPLEXITY_HIGH).
- Có thể gọi lookup_policy để tra chính sách xưởng trước khi quyết định.
- Mọi trường hợp còn lại -> allow_bot. Tín hiệu từ bước phân tích chỉ là gợi ý; bạn quyết định cuối.
"""

POLICY_TOOLS = [
    {"name": "handover_to_human", "description": "Chuyển cuộc trò chuyện cho nhân viên.",
     "input_schema": {"type": "object", "properties": {
         "reason": {"type": "string", "enum": HANDOVER_REASONS},
         "note": {"type": "string", "description": "Lý do ngắn gọn"}}, "required": ["reason"]}},
    {"name": "flag_safety_risk", "description": "Đánh dấu rủi ro an toàn để câu trả lời kèm cảnh báo và hotline.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": []}},
    {"name": "lookup_policy", "description": "Tra tài liệu chính sách/dịch vụ của xưởng.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "allow_bot", "description": "Bot tiếp tục xử lý bình thường.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]


def _signal_decision(interpretation) -> tuple[list[str], bool]:
    """Fallback decision taken directly from the preprocessing signals."""
    signals = interpretation.signals
    reasons = [reason for flag, reason in ((signals.wants_human, "HUMAN_REQUEST"), (signals.complaint, "COMPLAINT"))
               if flag]
    if interpretation.complexity == "HIGH" and any(
            task.domain in (Intent.KNOWLEDGE, Intent.MAINTENANCE) for task in interpretation.tasks):
        reasons.append("COMPLEXITY_HIGH")
    return reasons, signals.safety_risk


def _agent_decision(state: AgentState, interpretation) -> tuple[list[str], bool] | None:
    decision = {"reasons": [], "safety": False}

    def execute(name: str, args: dict) -> dict:
        if name == "handover_to_human":
            reason = args.get("reason") if args.get("reason") in HANDOVER_REASONS else "OTHER"
            decision["reasons"].append(reason)
            if reason == "SAFETY":
                decision["safety"] = True
        elif name == "flag_safety_risk":
            decision["safety"] = True
        elif name == "lookup_policy":
            chunks = get_services().knowledge.retrieve(str(args.get("query", "")), k=2)
            return {"documents": [chunk.text for chunk in chunks]}
        return {"ok": True}

    analysis = interpretation.model_dump(mode="json", include={"tasks", "signals", "complexity", "message_summary"})
    result = generate_with_tools(
        POLICY_PROMPT,
        f"Tin nhắn: {state.get('user_text', '')}\nPhân tích: {json.dumps(analysis, ensure_ascii=False)}",
        POLICY_TOOLS, execute, history=state.get("history"), max_turns=3,
    )
    if result.get("error") and not result.get("tool_calls"):
        return None
    return decision["reasons"], decision["safety"]


def policy_guard(state: AgentState) -> dict:
    interpretation = state.get("conversation_interpretation")
    negotiation = state.get("negotiation_active", False)
    reasons: list[str] = []
    safety = False
    if interpretation:
        # Business rule: discounts need manager approval, regardless of the LLM.
        if interpretation.signals.discount_request or any(t.domain == Intent.DISCOUNT for t in interpretation.tasks):
            negotiation = True
            reasons.append("NEGOTIATION")
        signals = interpretation.signals
        if signals.wants_human or signals.complaint or signals.safety_risk or interpretation.complexity == "HIGH":
            decision = _agent_decision(state, interpretation) or _signal_decision(interpretation)
            reasons += decision[0]
            safety = decision[1]
    reasons = list(dict.fromkeys(reasons))
    return {
        "negotiation_active": negotiation,
        "safety": safety,
        "route": "human" if reasons else "router",
        "handover_reason": "; ".join(reasons) or None,
        "trace": step(state, "policy_guard"),
    }
