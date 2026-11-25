# Đợt 4: L6 E2E, pytest.ini, CI

| File | Việc |
|---|---|
| tests/test_e2e.py | MỚI: 15 test E2E01-E2E09 (FakeLLM qua HTTP, ~5 giây, không tốn tiền) |
| pytest.ini | SỬA: thêm marker e2e, lọc DeprecationWarning của langsmith |
| Makefile | SỬA: thêm make test-e2e, make test-l5 |
| .github/workflows/tests.yml | MỚI: CI (pytest không LLM mỗi push/PR; L5 chạy tay) |

## Lệnh chạy
```bash
docker compose -f docker-compose.dev.yml up -d db        # Postgres test cổng 5433 (như Đợt 3B)

pytest tests/test_e2e.py -q                              # chỉ E2E
pytest tests/ -m e2e -q                                  # như trên, theo marker
pytest tests/ -q -p no:cacheprovider 2>&1 | tee reports/dot4/pytest_full.txt   # toàn bộ (mong đợi: 471 passed, 12 skipped, 23 xfailed)
```
Kết quả mong đợi của E2E: `15 passed`. Nếu đỏ, đọc kỹ assertion trước khi sửa (lỗi ở test hay ở code).

## Lưu ý
- CI chưa được chạy thử trên GitHub; lần đầu kiểm tra tab Actions. Secret `OPENAI_API_KEY` chỉ cần cho job L5 chạy tay.
- Test E2E07/E2E08 chạy đa luồng; nếu thấy chập chờn trên máy chậm, báo lại kèm log.
