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
| Intent Router | P0 ✅ | `app/agent/nodes/router.py` |
| Sales / Knowledge / Appointment Agent | P0 ✅ khung | `app/agent/nodes/*.py` |
| Contract Agent | P1 (khung rỗng) | `app/agent/nodes/contract.py`, `app/tools/contract.py` |
| Output Guard | P0 ✅ | `app/agent/nodes/output_guard.py` |
| Human Handover + Admin API | P0 tối thiểu ✅ | `app/agent/nodes/human_handover.py`, `app/api/admin.py` |
| Product / Knowledge / Booking / Handover Service | P0 ✅ khung | `app/services/` |
| CRM Sync + CRM webhook | P2 | — |
| Maintenance Service + Reminder Job + Outbox Worker | P1 | `app/jobs/reminders.py` |
| Birthday/Event Job, consent, DLQ | P2 | — |
| Postgres + pgvector | P0 dev bằng SQLite; Postgres khi cần | `app/db/` |
| Tracing/monitoring (Langfuse/LangSmith) | P1 — nên thêm sớm | — |
