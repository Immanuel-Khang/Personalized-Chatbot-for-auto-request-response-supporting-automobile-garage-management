"""[TRACK B] After-sales Agent: chỉ tra cứu lịch sử qua service, không truy cập DB."""
from app.agent.nodes.common import answer, step
from app.agent.state import AgentState
from app.services import get_services
from app.utils import fmt_vnd


def after_sales(state: AgentState) -> dict:
    records = get_services().after_sales.get_history(state.get("customer_id"), limit=5)
    if not records:
        reply = ("Hiện mình chưa có dữ liệu lịch sử bảo dưỡng của xe bạn trong hệ thống. "
                 "Bạn có thể cung cấp thêm thông tin hoặc nhờ nhân viên hỗ trợ tra cứu nhé.")
    else:
        lines = [f"- {r.service_date}: {r.description} ({fmt_vnd(r.cost_vnd)})" for r in records]
        reply = "Lịch sử bảo dưỡng của xe bạn:\n" + "\n".join(lines)
    return {"answers": [answer(state, "after_sales", reply)], "allowed_amounts": [fmt_vnd(r.cost_vnd) for r in records],
            "trace": step(state, "after_sales")}
