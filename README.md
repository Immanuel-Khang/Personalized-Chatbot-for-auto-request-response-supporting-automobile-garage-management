# Xưởng xe AI Agent — khung sườn

Bản khung **chạy được ngay** (không cần API key): web chat → FastAPI → LangGraph agent → DB.
Mọi chỗ cần viết thêm đều có dấu `TODO[TÊN-TRACK]`. Tìm bằng: `grep -rn "TODO\[" app/`

## Chạy thử (Khởi động nhanh với Docker)

Yêu cầu máy đã cài **Docker** và **Docker Compose**.

```bash
# 1. Tạo file cấu hình môi trường
cp .env.example .env

# 2. Khởi chạy môi trường phát triển (tự động build, mount code để live-reload, mở http://localhost:8000)
make dev

# Các lệnh quản lý khác:
make prod       # Chạy môi trường production ngầm (-d, multi-workers)
make down       # Dừng toàn bộ containers
make logs       # Xem log thời gian thực của container prod
make clean      # Dừng và xóa sạch volume dữ liệu (nếu muốn reset)
```

<details>
<summary><b>Hoặc chạy thủ công bằng Python local (không qua Docker)</b></summary>

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Postgres (có pgvector) chạy local
podman run -d --name garage-pg -e POSTGRES_USER=garage -e POSTGRES_PASSWORD=garage -e POSTGRES_DB=garage \
  -p 127.0.0.1:5432:5432 -v garage-pg-data:/var/lib/postgresql/data docker.io/pgvector/pgvector:pg17
podman exec garage-pg psql -U garage -c "CREATE DATABASE garage_test"   # DB cho pytest (bị xóa sạch mỗi lần chạy)
alembic upgrade head                 # tạo/cập nhật bảng (app cũng tự chạy lệnh này khi khởi động)
uvicorn app.main:app --reload        # mở http://localhost:8000
pytest                               # chạy test
python -m eval.run_eval              # chấm agent theo đường đi (trajectory)
```
</details>

Đổi schema: sửa `app/db/models.py` → `alembic revision --autogenerate -m "mô tả"` → đọc lại file trong
`migrations/versions/` → `alembic upgrade head`. Không sửa bảng bằng tay trên DB.

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
   → graph: preprocess → policy_guard → { human_handover | router }
            router ─Send→ agent chỉ đọc song song { sales | knowledge | after_sales }
                        + tối đa 1 agent ghi dữ liệu { contract | appointment }
            mọi agent → compose → output_guard → { respond | human_handover }
   → lưu messages (kèm trace) → trả lời
```
`trace` trả về trong mỗi response cho biết bot đi qua node nào → dùng để debug và để eval.
Thứ tự các nhánh song song trong trace không cố định.

- Preprocess tách tin thành `tasks: [{intent, text}]` (LLM trả `tasks` hoặc rule-based tách vế câu); mỗi agent chỉ đọc `task_text` của mình.
- Agent chỉ đọc chạy song song (tối đa 3). Agent ghi dữ liệu chạy tối đa 1, câu hỏi slot luôn nằm cuối câu trả lời.
- Hai agent ghi dữ liệu cùng lúc (vd chốt hợp đồng + đặt lịch): chạy contract, hoãn appointment vào `pending_intent` + `pending_text` và báo khách.
- Agent trả lời qua `answers` (reducer cộng dồn); `compose` ghép theo thứ tự vế câu bằng code, không để LLM viết lại số tiền.
- Một agent cần người không làm mất phần trả lời của agent khác: khách vẫn nhận giá, kèm thông báo chuyển nhân viên.
- Hỏi thêm câu chỉ đọc giữa flow đặt lịch không làm mất flow; hủy sẽ xóa flow, việc chờ và slot đặt lịch.
- `HUMAN_PENDING` và `HUMAN_ACTIVE` bỏ qua graph: chỉ lưu tin, cập nhật ticket/SĐT, nhân viên trả lời qua admin. Timeout vẫn gửi system message qua polling.
- After-sales hiện có node/protocol/service **stub** trả rỗng; chưa có bảng hoặc dữ liệu lịch sử thật.

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
| Retriever | **Hybrid** (BM25 + OpenAI embedding, RRF) trên `app/rag/`; chọn bằng `RETRIEVER` trong `.env` | pgvector + reranker + lọc metadata | A |
| DB | SQLite | Postgres (đổi `DATABASE_URL`) | A |
| Checkpoint | MemorySaver (mất khi tắt server) | PostgresSaver | B/C |
| Đặt lịch | ghi thẳng DB, chờ duyệt | giờ làm việc, slot, hủy/đổi | C |
| Hợp đồng / nhắc lịch | file rỗng có hướng dẫn | `app/tools/contract.py`, `app/jobs/reminders.py` | C |

Mức ưu tiên các khối sơ đồ: xem `docs/ARCHITECTURE.md`.
