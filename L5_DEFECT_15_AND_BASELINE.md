# L5 — DEFECT-15 + baseline 38/40 (2026-11-19)

Nguồn: `reports/l5/l5_20261119_203017.json`  
Model: `gpt-6-luna`, reasoning_effort=`low`  
Task success: **38/40 = 95%** | Tool P/R: 93%/100% | Lỗi giá: 0 | P50/P95: 6.5s / 8.3s

## Quyết định đã chốt (chấm tay B7)

### BK_CANCEL_CREATED → DEFECT-15 (giữ FAIL)

**Mô tả:**  
Sau khi khách xác nhận hủy lần 2 (“Đúng, hủy đi”), bot chỉ hỏi lại xác nhận thêm lần nữa, **không gọi `cancel_appointment`** và không cập nhật DB. Lịch vẫn ở trạng thái hiệu lực (appointments=1, cancelled=0).

**Luồng quan sát:**
1. Đặt lịch đầy đủ → bot đề xuất → khách “Đúng rồi, xác nhận” → đặt thành công.
2. “Hủy lịch hẹn giúp mình” → bot hỏi “Bạn xác nhận muốn hủy…”.
3. “Đúng, hủy đi” → bot **lại** hỏi xác nhận, DB không đổi.

**Mức:** Critical (ảnh hưởng R02 – đặt/hủy lịch).  
**Hành động:** Giữ assertion `expect_db: {appointments: 0, cancelled: 1}` ở lượt 4. Không nới test. Ghi nhận defect, không sửa production trong Đợt 3.

### BK_SHOWROOM_FULL → giữ FAIL (forbid sales)

Khách gửi một tin đầy đủ (showroom + mẫu + giờ + tên + SĐT). Bot chạy thêm node `sales` (liệt kê mẫu) bên cạnh `appointment`.  
Test cấm `sales` để kiểm tra: khi đủ thông tin đặt lịch thì ưu tiên appointment, không lan sang sales.  
Quyết định: **giữ `forbid_path: ["sales"]`**. Bot vẫn đặt lịch đúng ở lượt 2, nhưng vi phạm kỳ vọng orchestration của ca này.

## Các chỉ số đạt mục tiêu giữa kỳ (một lượt)

| Chỉ số | Kết quả | Mục tiêu |
|--------|---------|----------|
| Task success | 38/40 (95%, CI95 83–99%) | ≥ 70% |
| Tool-call precision | 40/43 (93%) | ≥ 85% |
| Tool-call recall | 17/17 (100%) | ≥ 80% |
| Lỗi giá | 0/52 lượt | 0 |
| Latency P50 / P95 | 6.5s / 8.3s | P50 < 10s |

Hai ca còn FAIL đều thuộc nhóm booking (critical). Không có critical FAIL khác (an toàn, giá, handover đều ổn).

## Chấm tay B7 (tóm tắt)

- An toàn (đèn dầu, mất phanh, khói): đạt 5/5 — có hotline, dừng xe, không khuyến khích tiếp tục lái, handover đúng.
- Giá / ngân sách / so sánh: grounded catalog, không bịa.
- Hủy lịch: defect (DEFECT-15).
- Knowledge: nội dung đúng tài liệu; một số ca route qua conversation nhưng chấp nhận được sau triage trước.
- OOS / negotiation / history: xử lý đúng chính sách.

## Việc tiếp theo

1. Chạy lại full 40 ca **một lượt** để xác nhận baseline ổn định (kỳ vọng vẫn 38/40).
2. Chạy **L5_REPEAT=3** để lấy số liệu chính thức cho báo cáo giữa kỳ.
3. Không sửa production code cho DEFECT-15 trong Đợt 3.
4. L4 BM25 đã chọn làm baseline — không cần chạy lại trừ khi đổi knowledge.

