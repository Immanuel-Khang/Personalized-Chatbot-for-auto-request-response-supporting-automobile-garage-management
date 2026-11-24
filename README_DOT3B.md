# Đợt 3B: kiểm thử lại sau thay đổi task dependency

Giải nén zip này **đè lên thư mục gốc của project** (bản _40). Kế hoạch chi tiết: Phần C trong file kế hoạch .docx.

## File trong bộ này

| File | Loại | Ghi chú |
|---|---|---|
| `app/main.py` | SỬA code app | Lifespan gọi `get_graph()` trước khi nhận request (sửa lỗi F1: treo request chat đầu tiên trên DB trống). Cần nhóm duyệt |
| `tests/test_unit_dependency.py` | MỚI | 22 test L1 (DU01-DU22) |
| `tests/test_inv_dependency.py` | MỚI | 14 test L3 (DP01-DP14) |
| `tests/test_contract_price.py` | MỚI | 8 test (CP01-CP08) |
| `tests/test_memory_cursor.py` | MỚI | 9 test (MC01-MC09) |
| `tests/test_startup_graph.py` | MỚI | 2 test (ST01-ST02) |
| `tests/test_conv_history.py` | SỬA | Assertion chấp nhận khóa `id` mới (F2) |
| `tests/test_l5_units.py` | SỬA | Bộ benchmark giờ là 50 ca, thêm nhóm `dependency` |
| `tests/test_l5_benchmark.py` | SỬA | Chỉ sửa chú thích đầu file |
| `benchmark/l5_cases.json` | SỬA | Thêm 10 ca `DP_*` ở cuối; 40 ca gốc không đổi |

## Bước 1: Postgres + pytest (không tốn tiền)

```bash
docker compose -f docker-compose.dev.yml up -d db
pg_isready -h 127.0.0.1 -p 5433          # phải báo accepting connections
pip install -r requirements.txt
mkdir -p reports/dot3b

# Chỉ các test của Đợt 3B (khoảng 10 giây)
LLM_PROVIDER=none pytest tests/test_unit_dependency.py tests/test_inv_dependency.py tests/test_contract_price.py \
  tests/test_memory_cursor.py tests/test_startup_graph.py tests/test_conv_history.py tests/test_l5_units.py -q

# Toàn bộ suite (khoảng 1 phút)
LLM_PROVIDER=none pytest tests/ -q -rfE 2>&1 | tee reports/dot3b/pytest_full.txt
```

Kết quả mong đợi: `456 passed, 12 skipped, 23 xfailed` (50 ca L5 bị loại khỏi lần chạy mặc định).
Nếu pytest **treo** ở test chat đầu tiên: bạn chưa chép `app/main.py` mới (lỗi F1).

## Bước 2: L5 nhóm dependency, 1 lần (tốn tiền ít, cần API key trong .env)

```bash
RUN_LLM_TESTS=1 L5_GROUPS=dependency pytest tests/test_l5_benchmark.py -m llm -q -rfE 2>&1 | tee reports/dot3b/l5_dependency_x1.txt
```

Xem từng ca đỏ trước khi chạy 3 lần. Báo cáo sinh ra ở `reports/l5/l5_<giờ>.json` và `.md`.

## Bước 3: L5 toàn bộ 50 ca, lặp 3 lần

```bash
RUN_LLM_TESTS=1 L5_REPEAT=3 pytest tests/test_l5_benchmark.py -m llm -q -rfE 2>&1 | tee reports/dot3b/l5_full_x3.txt
```

Chạy Judge và tổng hợp như Đợt 3 (xem `README_REPEAT3.md`).
Để so 40 ca gốc với kết quả cũ (114/120), lọc nhóm:

```bash
RUN_LLM_TESTS=1 L5_REPEAT=3 L5_GROUPS=sales,knowledge,booking,policy,multi,oos \
  pytest tests/test_l5_benchmark.py -m llm -q 2>&1 | tee reports/dot3b/l5_base40_x3.txt
```

## Bước 4: gửi lại để viết báo cáo Đợt 3B

`reports/dot3b/*.txt` và các file mới trong `reports/l5/`.
