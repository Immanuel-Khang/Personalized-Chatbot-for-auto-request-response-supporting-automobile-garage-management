"""[TRACK B] Knowledge Agent: RAG + maintenance lookup + lời khuyên an toàn.
Tích hợp maintenance_node từ codebase hiện tại (tra cứu mốc km bảo dưỡng)."""
from app.agent.llm import generate
from app.agent.nodes.common import step
from app.agent.state import AgentState
from app.config import settings
from app.contracts.schemas import Intent
from app.services import get_services
from app.utils import fmt_vnd

SAFETY_PREFIX = f"⚠️ Nếu xe có dấu hiệu nguy hiểm, hãy dừng xe ở nơi an toàn và gọi hotline {settings.hotline}.\n"

KNOWLEDGE_SYSTEM_PROMPT = """\
Bạn là chuyên viên kỹ thuật ô tô Toyota. Trả lời chính xác dựa trên tài liệu được cung cấp.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. KHÔNG ĐƯỢC bịa thông tin. Nếu tài liệu không có → nói thật.
3. Nếu liên quan đến an toàn → nhấn mạnh cảnh báo và hotline.
4. Nếu có thông tin bảo dưỡng theo km → nêu rõ mốc km, công việc cần làm, chi phí ước tính.
"""


def knowledge(state: AgentState) -> dict:
    update: dict = {"trace": step(state, "knowledge")}
    if state.get("complexity") == "HIGH":
        return {**update, "needs_human": True, "handover_reason": "COMPLEXITY_HIGH"}

    slots = state.get("slots", {})
    intents = state.get("intents", [])
    extra_context = ""

    # --- Port maintenance_node: tra cứu mốc km bảo dưỡng ---
    if Intent.MAINTENANCE.value in intents:
        car_model = slots.get("car_model", "Toyota")
        mileage = slots.get("current_mileage_km")
        if mileage:
            result = get_services().maintenance.get_milestone_details(car_model, int(mileage))
            if result:
                tasks_str = "\n".join(f"  • {t}" for t in result.tasks)
                extra_context = (
                    f"\n📋 Lịch bảo dưỡng mốc {result.km_milestone:,} km cho {result.model_name}:\n"
                    f"{tasks_str}\n"
                    f"💰 Chi phí ước tính: {fmt_vnd(int(result.estimated_cost))}\n"
                )

    # --- RAG: tìm kiến thức từ tài liệu ---
    chunks = get_services().knowledge.retrieve(state["user_text"], k=3)
    if not chunks and not extra_context:
        body = "Mình chưa có thông tin chính xác về vấn đề này. Bạn để lại số điện thoại để nhân viên kỹ thuật hỗ trợ nhé."
    else:
        context = "\n".join(c.text for c in chunks) if chunks else ""
        full_context = context + extra_context

        # Dùng LLM sinh câu trả lời
        body = generate(
            KNOWLEDGE_SYSTEM_PROMPT,
            f"Câu hỏi: {state['user_text']}\n\nTài liệu:\n{full_context}"
        ) or (extra_context if extra_context else f"Theo tài liệu của xưởng: {chunks[0].text}")

    prefix = SAFETY_PREFIX if state.get("safety") else ""
    return {**update, "draft_reply": prefix + body}
