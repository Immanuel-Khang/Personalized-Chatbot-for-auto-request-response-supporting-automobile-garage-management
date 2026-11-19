# Baseline Đợt 1: kết quả đo ban đầu

Nguồn số liệu: `reports/baseline/after2_pytest_full.txt` (pytest) và `reports/baseline/rag_eval.txt` (RAG).

## Môi trường đo
- Python 3.14, venv `.venv`; Postgres (pgvector) cổng 5433, DB `garage_test` (xóa sạch mỗi lần chạy).
- `LLM_PROVIDER=none`, `EMBEDDING_PROVIDER=none` (không gọi OpenAI).
- Lệnh: `pytest tests/ -q -rA --durations=20` (có `pytest.ini`: `testpaths = tests`).

## Kết quả pytest
| Chỉ số | Giá trị |
|---|---|
| Passed | 167 |
| Skipped (cần LLM/FakeLLM) | 93 |
| Xfailed (lỗi đã biết) | 11 |
| Failed | 0 |
| Thời gian | 26,9 giây |

**Lưu ý khi đọc số:** 93 test skipped là toàn bộ hội thoại và smoke. Từ khi `preprocess` chỉ dùng LLM, chế độ `LLM_PROVIDER=none` không còn hiểu được tin nhắn (bot trả "mình chưa hiểu rõ ý bạn"). Vì vậy tầng L3 (hội thoại) **chưa được đo**, không được tính là "đạt".

### Skipped theo file
| File | Số test |
|---|---|
| test_agent_boundaries.py | 22 |
| test_conv_policy.py | 21 |
| test_smoke.py | 17 |
| test_conv_sales_discovery.py | 11 |
| test_conv_booking.py | 9 |
| test_conv_context.py | 5 |
| test_conv_sales.py | 3 |
| test_conv_inquiry.py | 2 |
| test_conv_history.py | 1 |
| test_conv_pending.py | 1 |
| test_conversation_agent.py | 1 |
| **Tổng** | **93** |

Danh sách đầy đủ: `tests/needs_llm.txt`. Đây là việc đầu tiên của Đợt 2 (dựng FakeLLM).

### Xfailed (11)
| Test | Lý do |
|---|---|
| test_conv_booking::test_tc07_cancelling_created_appointment_is_not_falsely_confirmed | DEFECT-07: chưa có luồng hủy lịch đã tạo |
| test_conv_context::test_tc11_follow_up_is_not_a_verbatim_repeat | DEFECT-11: knowledge không dùng lịch sử, lặp nguyên văn |
| test_conv_inquiry::test_tc03_unknown_price_is_flagged_not_substituted | DEFECT-03: tìm sản phẩm ILIKE theo từng từ, báo giá sai món |
| test_customer_name::test_llm_name_with_phone_in_same_message_is_cleaned | Lỗi mới: preprocess không còn lọc giá trị không phải tên |
| test_customer_name::test_llm_non_name_update_is_discarded (5 tham số: "10 tấn", "xe tải", "ngày mai", "0912345678", "Xin chào") | Cùng lỗi trên |
| 2 test `appointment()` trong test_customer_name.py | Gọi node trực tiếp với state tối thiểu, contract đã đổi (suy ra từ số liệu 11 xfail; xác nhận bằng `pytest tests/ -rx`) |

## Kết quả RAG (22 câu trong phạm vi + 4 ngoài phạm vi)
| Chỉ số | keyword | bm25 |
|---|---|---|
| Recall@1 | 0.77 | 0.95 |
| Recall@3 | 0.82 | 1.00 |
| Recall@5 | 0.86 | 1.00 |
| MRR@5 | 0.81 | 0.98 |
| Chuyển oan (thấp tốt) | 0.14 | 0.00 |
| Trả bừa (thấp tốt) | 0.25 | 0.50 |

- `keyword` trượt toàn bộ nhóm câu không dấu (Recall@3 = 0.00).
- Cả hai còn "trả bừa": "thời tiết hôm nay ở Hà Nội" và "lãi suất vay mua xe trả góp" vẫn trả chunk, nên bot không chuyển cho nhân viên. Cần chỉnh ngưỡng ở Đợt 3.
- Mẫu nhỏ (26 câu), chỉ dùng làm mốc so sánh.

## Bug thật đã tìm và sửa trong Đợt 1
- `booking.py::reschedule_appointment`: nhả slot (`slot_id = NULL`) khi `status` còn `CONFIRMED` nên autoflush vi phạm ràng buộc `ck_appointments_slot_required_when_confirmed`. Đổi lịch hẹn đã xác nhận ném `IntegrityError`. Đã sửa bằng cách đặt `PENDING_APPROVAL` trước khi nhả slot.

## Lỗi mới chưa sửa
- Preprocess LLM-only không còn lọc tên khách: "xe tải", "0912345678", "ngày mai", "Xin chào" có thể bị lưu làm tên. Đề xuất mã DEFECT-12.

## Phạm vi baseline theo tầng
| Tầng | Trạng thái |
|---|---|
| L1 Unit, L2 Service/API | Có baseline (167 passed) |
| L4 RAG | Có baseline (bảng trên) |
| L3 Hội thoại/agent | Chưa đo được (93 skipped chờ FakeLLM) |
| L5 LLM thật, L6 E2E | Chưa làm (Đợt 3, 4) |
