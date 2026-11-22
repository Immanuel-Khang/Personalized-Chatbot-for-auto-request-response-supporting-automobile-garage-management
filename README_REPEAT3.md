# L5 — Chạy baseline xác nhận + Repeat 3

Áp dụng file trong zip này (ghi đè nếu trùng tên):

- `L5_DEFECT_15_AND_BASELINE.md`  → ghi nhận defect + kết quả chấm tay B7

Không cần sửa `benchmark/l5_cases.json` hay `tests/l5.py` cho hai ca còn FAIL (đã chốt giữ nguyên).

## Bước 1 — Xác nhận baseline 1 lượt (khuyến nghị)

```bash
RUN_LLM_TESTS=1 L5_BUDGET_USD=1.00 pytest tests/test_l5_benchmark.py -m llm -q -W ignore
```

Kỳ vọng: **38 passed, 2 failed** (BK_CANCEL_CREATED + BK_SHOWROOM_FULL).  
Lưu lại `reports/l5/l5_*.json` và `.md` mới.

## Bước 2 — Repeat 3 (số liệu chính thức)

Chỉ chạy khi bước 1 ổn định (vẫn 38/40):

```bash
RUN_LLM_TESTS=1 L5_REPEAT=3 L5_BUDGET_USD=3.00 pytest tests/test_l5_benchmark.py -m llm -q -W ignore
```

- Mỗi ca chạy 3 lần → tổng ~120 ca-lượt.
- Chi phí ước tính: khoảng 3 × $0.018 ≈ **$0.05–0.06** (trần đặt 3.00 để an toàn).
- Báo cáo cuối sẽ có pass-rate theo ca và theo nhóm, kèm CI.

Sau khi chạy xong, gửi:

- `reports/l5/l5_*.md` (báo cáo mới nhất)
- `reports/l5/l5_*.json` (chi tiết)

để tổng hợp 5 chỉ số giữa kỳ cuối cùng.

## Lưu ý

- Model cố định: `gpt-6-luna`, `reasoning_effort=low`.
- Không đổi assertion của 2 ca FAIL.
- DEFECT-15 đã ghi trong `L5_DEFECT_15_AND_BASELINE.md`.
