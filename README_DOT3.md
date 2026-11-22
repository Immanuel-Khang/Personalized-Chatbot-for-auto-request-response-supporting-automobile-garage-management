# Đợt 3: đo bằng LLM thật (gpt-6-luna, ngân sách ~$2.31)

## Đã có trong bộ này
| File | Việc |
|---|---|
| `tests/llm_budget.py` | Đếm token/tiền, trần chi `L5_BUDGET_USD` (mặc định 1.50), `reasoning_effort=low` |
| `tests/l5.py` | Chấm điểm bằng code, Wilson CI, P50/P95, tool-call precision/recall, ghi báo cáo |
| `tests/test_l5_benchmark.py` | 40 ca L5 (marker `llm`, không chạy mặc định) |
| `benchmark/l5_cases.json` | 40 ca: sales 8, knowledge 8, booking 8, policy 8, multi 4, oos 4 |
| `tests/test_l5_units.py` | 10 test kiểm phần chấm điểm bằng dữ liệu giả (chạy mỗi commit, miễn phí) |
| `tests/conftest.py`, `pytest.ini` | Marker `llm`, trần ngân sách, in chi phí + ghi báo cáo cuối lần chạy |

## Thứ tự chạy (tiết kiệm trước)
```bash
# 0) Miễn phí: kiểm bộ chấm điểm và suite thường (llm bị loại mặc định)
pytest tests -q -W ignore                      # mong đợi 396 passed, 12 skipped, 23 xfailed, 40 deselected

# 1) L4 RAG: BM25 miễn phí; vector/hybrid tốn rất ít (embedding). Không qua pytest.
python -m eval.run_rag_eval --configs keyword,bm25 -v
python -m eval.run_rag_eval --configs keyword,bm25,hybrid -v     # cần EMBEDDING_PROVIDER=openai trong .env

# 2) Thử nghiệm 3 ca để biết chi phí thật (ghi lại số $ in ra, đối chiếu dashboard OpenAI)
RUN_LLM_TESTS=1 pytest tests/test_l5_benchmark.py -m llm -q -W ignore \
  -k "BK_FULL_CONFIRM or PL_DISCOUNT or SL_PRICE_BRAND"

# 3) 12 test còn skip (cần LLM thật), ~ vài chục lượt
RUN_LLM_TESTS=1 pytest tests/test_conv_sales_discovery.py \
  tests/test_conv_context.py::test_tc12_oil_change_price_is_not_a_vehicle_price -q -W ignore

# 4) Benchmark theo nhóm rồi mới chạy hết (mỗi lệnh có trần $1.50 riêng; đặt thấp hơn nếu muốn)
RUN_LLM_TESTS=1 L5_BUDGET_USD=0.40 L5_GROUPS=policy,oos pytest tests/test_l5_benchmark.py -m llm -q -W ignore
RUN_LLM_TESTS=1 L5_BUDGET_USD=0.80 pytest tests/test_l5_benchmark.py -m llm -q -W ignore
```
Cuối mỗi lần chạy: dòng chi phí `LLM: N lần gọi, ... ≈ $x` và báo cáo `reports/l5/l5_<giờ>.md` + `.json`.
Chạm trần chi thì pytest dừng (mã thoát 3) và vẫn ghi báo cáo một phần.

## Quy tắc
- `.env` phải có `LLM_PROVIDER=openai` và key; test tự đặt `EMBEDDING_PROVIDER=none` (RAG chỉ BM25, không tốn embedding).
- Chạy lặp (`L5_REPEAT=3`) chỉ sau khi biết chi phí 1 lượt; mỗi lần lặp nhân đôi/ba chi phí.
- Ca FAIL ở L5 chưa chắc là lỗi bot: có thể ground truth/regex của ca chưa đúng. Đọc `reports/l5/*.json` (có câu trả lời
  từng lượt) rồi sửa ca hoặc ghi lỗi `@defect`; đừng sửa assertion chỉ để xanh.
- Chưa có LLM Judge và chấm tay (kế hoạch B7 bước 2-3): làm khi có thêm ngân sách.
- Số liệu phải ghi rõ model `gpt-6-luna`, reasoning_effort `low`, temperature 1 (đã nằm trong báo cáo).

## Bản sửa sau thử 3 ca (19/11/2026)
- `tests/l5.py`: bắt số tiền viết tắt và mọi đuôi (800 triệu, 1,2 tỷ, 600tr, 399.000.000đ, VND) và so theo giá trị; số khách tự nêu (ngân sách) bot nhắc lại thì hợp lệ. So khớp chính xác nên bot làm tròn giá sẽ bị báo: đọc báo cáo trước khi kết luận lỗi giá.
- Tool-call: cùng node + công cụ + đối số lặp trong một lượt chỉ tính một lần (đúng B8); thêm `ok_tools` cho nhóm booking, policy, multi; số ca `needs_tool` từ 10 lên 17 (thêm ca an toàn, khiếu nại, hỏi giá kèm ý khác). Các ca mới này là suy ra từ prompt policy guard, cần xác nhận khi chạy.
- `req` của 40 ca đã đổi theo bảng B11 (R01 giá, R02 đặt lịch, R03 kiến thức và an toàn, R04 không hứa giảm giá, R05 nhiều ý, R06 chuyển nhân viên, R07 lịch sử, R08 hủy/đổi chủ đề; '-' = không gắn yêu cầu).
- `KN_NOT_IN_DOCS`: giá bugi xe Hyundai không có trong tài liệu, nên bot không được nêu số tiền nào và phải nói chưa có thông tin hoặc chuyển nhân viên.
- Test mới: 4 test cho phần số tiền, dedupe, và kiểm tên công cụ/req trong file ca.
