"""Answer technical, maintenance, and service questions from trusted sources."""
from app.agent.llm import generate
from app.agent.nodes.compose import STYLE_RULES
from app.agent.state import AgentState, answer, fact_value
from app.config import settings
from app.contracts.schemas import Intent
from app.services import get_services
from app.utils import fmt_vnd

SAFETY_PREFIX = f"⚠️ Nếu xe có dấu hiệu nguy hiểm, hãy dừng xe ở nơi an toàn và gọi hotline {settings.hotline}.\n"

KNOWLEDGE_SYSTEM_PROMPT = """\
Bạn là chuyên viên kỹ thuật ô tô của xưởng Foton. Trả lời chính xác dựa trên tài liệu được cung cấp.
QUY TẮC:
1. Luôn trả lời bằng tiếng Việt.
2. KHÔNG ĐƯỢC bịa thông tin. Nếu tài liệu không có → nói thật.
3. Nếu liên quan đến an toàn → nhấn mạnh cảnh báo và hotline.
4. Nếu có thông tin bảo dưỡng theo km → nêu rõ mốc km, công việc cần làm, chi phí ước tính.
""" + STYLE_RULES


def knowledge(state: AgentState) -> dict:
    update: dict = {"trace": ["knowledge"]}
    facts = state.get("known_facts", {}) or {}
    interpretation = state.get("conversation_interpretation")
    tasks = interpretation.tasks if interpretation else []
    maintenance = any(task.domain == Intent.MAINTENANCE for task in tasks)
    extra_context = ""
    result = None

    if maintenance:
        car_model = fact_value(facts, "car_model") or "xe của bạn"
        mileage = fact_value(facts, "current_mileage_km")
        if mileage:
            result = get_services().maintenance.get_milestone_details(car_model, int(mileage))
            if result:
                tasks_str = "\n".join(f"  • {task}" for task in result.tasks)
                extra_context = (
                    f"\n📋 Lịch bảo dưỡng mốc {result.km_milestone:,} km cho {result.model_name}:\n"
                    f"{tasks_str}\n💰 Chi phí ước tính: {fmt_vnd(int(result.estimated_cost))}\n"
                )

    question = state.get("task_text") or state["user_text"]
    chunks = get_services().knowledge.retrieve(question, k=3)
    if not chunks and not extra_context:
        update["answers"] = [answer(state, "knowledge", needs_human=True,
                                    handover_reason="COMPLEXITY_HIGH: no RAG match")]
        return update

    context = "\n".join(chunk.text for chunk in chunks) if chunks else ""
    full_context = context + extra_context
    body = generate(
        KNOWLEDGE_SYSTEM_PROMPT,
        f"Câu hỏi: {question}\n\nTài liệu:\n{full_context}",
        history=state.get("history"),
    ) or (extra_context if extra_context else f"Theo tài liệu của xưởng: {chunks[0].text}")

    prefix = SAFETY_PREFIX if state.get("safety") else ""
    update["answers"] = [answer(state, "knowledge", prefix + body)]
    if result:
        update["allowed_amounts"] = [fmt_vnd(int(result.estimated_cost))]
    return update
