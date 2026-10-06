# Xưởng xe AI Agent — khung sườn

Bản khung **chạy được ngay** (không cần API key): web chat → FastAPI → LangGraph agent → DB.
Mọi chỗ cần viết thêm đều có dấu `TODO[TÊN-TRACK]`. Tìm bằng: `grep -rn "TODO\[" app/`

## Chạy thử (5 phút)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload        # mở http://localhost:8000
pytest                               # chạy test
python -m eval.run_eval              # chấm agent theo đường đi (trajectory)
```

## Bản đồ thư mục & ai sở hữu

| Thư mục / file | Việc | Chủ |
|---|---|---|
| `app/rag/`, `app/db/`, `app/services/product.py`, `app/services/knowledge.py`, `data/` | RAG, schema DB, dữ liệu xe/giá/kiến thức | **A** |
| `app/agent/`, `eval/` | LangGraph: preprocess, policy, router, các agent, guard, eval | **B** |
| `app/api/`, `app/conversation.py`, `app/identity.py`, `app/services/booking.py`, `handover.py`, `app/tools/`, `app/jobs/`, `web/` | API, hội thoại, đặt lịch, handover, hợp đồng, nhắc lịch, giao diện | **C** |
| `app/contracts/` | **Hợp đồng** giữa 3 track | **Cả 3** |

## 3 luật để khỏi vỡ khi ghép
1. **Chỉ sửa thư mục của mình.** Cần sửa chỗ người khác → nhắn họ hoặc mở PR cho họ review.
2. **`app/contracts/` là hợp đồng.** Đổi file này phải được cả 3 người đồng ý. Agent (B) chỉ gọi service qua các hàm trong `interfaces.py`, không import DB/RAG trực tiếp.
3. **Mỗi tính năng = 1 nhánh + 1 PR + 1 test.** Nhánh: `feature/<track>-<việc>`. `main` luôn phải chạy được (`pytest` xanh).

## Luồng 1 tin nhắn
```
web → POST /chat → conversation.py (khóa, kiểm tra mode)
   → graph: preprocess → policy_guard → { human_handover | knowledge | router }
            router → { sales | contract | appointment | knowledge | respond }
            agent → output_guard → { respond | human_handover }
   → lưu messages (kèm trace) → trả lời
```
`trace` trả về trong mỗi response cho biết bot đi qua node nào → dùng để debug và để eval.

## Thêm 1 tính năng mới: làm theo thứ tự này
1. Ghi yêu cầu vào `docs/REQUIREMENTS.md` (user story + tiêu chí hoàn thành).
2. Cần dữ liệu/hàm mới giữa các track? Sửa `app/contracts/` trước (cả nhóm duyệt).
3. Viết **test hoặc eval case** trước (`tests/`, `eval/cases.json`).
4. Viết code trong thư mục của mình; chạy `pytest` + `python -m eval.run_eval`.
5. Mở PR → người khác review → merge → demo 5 phút cuối tuần.

## Những thứ đang là "bản khung" (thay dần)
| Phần | Hiện tại | Thay bằng | Track |
|---|---|---|---|
| Intent/slot | rule-based từ khóa | LLM structured output | B |
| Viết câu trả lời | ghép chuỗi từ dữ liệu | `agent/llm.py` + ReAct ≤3 vòng | B |
| Retriever | khớp từ khóa | embedding + Chroma/pgvector | A |
| DB | SQLite | Postgres (đổi `DATABASE_URL`) | A |
| Checkpoint | MemorySaver (mất khi tắt server) | PostgresSaver | B/C |
| Đặt lịch | ghi thẳng DB, chờ duyệt | giờ làm việc, slot, hủy/đổi | C |
| Hợp đồng / nhắc lịch | file rỗng có hướng dẫn | `app/tools/contract.py`, `app/jobs/reminders.py` | C |

Mức ưu tiên các khối sơ đồ: xem `docs/ARCHITECTURE.md`.
