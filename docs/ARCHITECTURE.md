# Sơ đồ kiến trúc → code, và mức ưu tiên

- **P0** = có trong khung sườn / bắt buộc cho demo
- **P1** = làm sau khi P0 ổn
- **P2** = ghi vào báo cáo là "hướng phát triển", chưa làm

| Khối trong sơ đồ | Trạng thái | File |
|---|---|---|
| Web Chat | P0 ✅ khung | `web/index.html` |
| Zalo OA / ZNS | P2 (đã chốt dùng web chat) | — |
| FastAPI Gateway (rate limit, idempotency, async) | P0 tối thiểu ✅ / P1 phần còn lại | `app/api/chat.py` |
| Identity (OTP, merge) | P0 ẩn danh ✅ / P2 OTP+merge | `app/identity.py` |
| Conversation Manager (lock, mode) | P0 ✅ | `app/conversation.py` |
| Preprocess | P0 ✅ rule-based → LLM | `app/agent/nodes/preprocess.py` |
| Policy Guard | P0 ✅ | `app/agent/nodes/policy_guard.py` |
| Intent Router (lập kế hoạch song song, ≤1 agent ghi/lượt) + Compose | P0 ✅ | `app/agent/nodes/router.py` |
| Sales / Knowledge / Appointment Agent | P0 ✅ khung | `app/agent/nodes/*.py` |
| After-sales Agent / Service | P0 ✅ node + service stub, dữ liệu ServiceHistory là P1 | `app/agent/nodes/after_sales.py`, `app/services/after_sales.py` |
| Contract Agent | P1 (slot-filling, chưa xuất tài liệu) | `app/agent/nodes/contract.py`, `app/tools/contract.py` |
| Output Guard | P0 ✅ | `app/agent/nodes/output_guard.py` |
| Human Handover + Admin API | P0 tối thiểu ✅ | `app/agent/nodes/human_handover.py`, `app/api/admin.py` |
| Product / Knowledge / Booking / Handover Service | P0 ✅ khung | `app/services/` |
| CRM Sync + CRM webhook | P2 | — |
| Maintenance Service + Reminder Job + Outbox Worker | P1 | `app/jobs/reminders.py` |
| Birthday/Event Job, consent, DLQ | P2 | — |
| Postgres + pgvector | P0 dev bằng SQLite; Postgres khi cần | `app/db/` |
| Tracing/monitoring (Langfuse/LangSmith) | P1 — nên thêm sớm | — |

## Ranh giới Layer 3

Preprocess chỉ tách intent, task theo vế câu, slot và complexity (LLM structured output hoặc rule-based), không chọn agent hay tạo side effect. Policy Guard quản lý giảm giá, yêu cầu gặp người, khiếu nại, safety và complexity, và ghi đủ mọi lý do chuyển người. Router là nơi duy nhất lập kế hoạch: agent chỉ đọc (sales, knowledge, after_sales) chạy song song qua `Send`, agent ghi dữ liệu (contract, appointment) tối đa một mỗi lượt. Safety luôn chạy knowledge một mình, đọc cả câu.

Các nhánh song song chỉ ghi vào key có reducer (`answers`, `allowed_amounts`, `trace`); agent ghi dữ liệu duy nhất mới ghi `slots`/`active_flow`. `compose` ghép câu trả lời theo thứ tự task rồi mới qua Output Guard; Output Guard kiểm tra bản đã ghép với `allowed_amounts` gộp từ mọi nhánh, vi phạm thì bỏ toàn bộ bản nháp. Human handover giữ phần trả lời hợp lệ của agent khác, rồi xóa flow/intent chờ trước khi bàn giao.

`pending_intent`, `active_flow`, slots dùng MemorySaver nên mất khi restart; chưa có checkpoint bền vững. Bot không chạy trong cả hai human mode; tin khách vẫn vào inbox và ticket đang mở. After-sales chỉ đọc qua service, không truy cập DB trực tiếp và không bịa lịch sử.

Lệch so với sơ đồ gốc: nguyên tắc "one bounded agent per turn" đổi thành "tối đa một agent ghi dữ liệu mỗi lượt", để trả lời được tin nhắn nhiều câu hỏi trong một lượt.
