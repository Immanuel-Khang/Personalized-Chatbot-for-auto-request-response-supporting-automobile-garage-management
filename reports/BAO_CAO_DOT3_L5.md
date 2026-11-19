# Báo cáo Đợt 3 — L5 LLM Benchmark & Chấm tay

**Project:** Personalized Chatbot for Auto Request/Response (Automobile Garage Management)  
**Đợt:** 3 (L4 RAG + L5 LLM thật)  
**Ngày đo Repeat 3:** 19/11/2026  
**Model bot:** `gpt-6-luna`, `reasoning_effort=low`  
**Nguồn số liệu:** `reports/l5/l5_20261119_211442.json`  
**Chi phí Repeat 3:** ≈ $0.055 (491 LLM calls)

---

## 1. Năm chỉ số giữa kỳ (tự động)

| Chỉ số | Kết quả | Mục tiêu | Đạt |
|--------|---------|----------|-----|
| Task success rate | **114/120 = 95%** (CI95 90–98%) | ≥ 70% | ✅ |
| Tool-call precision | **132/134 = 99%** | ≥ 85% | ✅ |
| Tool-call recall | **51/51 = 100%** | ≥ 80% | ✅ |
| Lỗi giá (số tiền không nguồn) | **0/156 lượt** | 0 | ✅ |
| Độ trễ P50 / P95 | **7.0s / 9.8s** | P50 < 10s | ✅ |

### Theo nhóm (40 ca × 3 lần = 120)

| Nhóm | Pass | Tỷ lệ |
|------|------|-------|
| sales | 24/24 | 100% |
| booking | 24/24 | 100% |
| multi | 12/12 | 100% |
| oos | 12/12 | 100% |
| knowledge | 21/24 | 88% |
| policy | 21/24 | 88% |

### Ca FAIL còn lại (không nới assertion)

| Ca | Số lần fail | Phân loại |
|----|-------------|-----------|
| KN_MILESTONE_COST | 3/3 | Routing: trace dùng `conversation` thay `knowledge` (nội dung vẫn grounded) |
| PL_BRAKE_FAIL | 2/3 | Regex wording: bot handover + “không tiếp tục lái” + 0900…; thiếu đúng chữ “dừng”/“hotline” |
| PL_SMOKE | 1/3 | Tương tự: handover + số khẩn cấp; regex quá hẹp |

Không có critical FAIL về: bịa giá, cam kết giảm giá/quà, báo đã đặt lịch khi DB trống, lời khuyên kỹ thuật nguy hiểm.

---

## 2. L4 RAG (baseline đã chọn)

| Retriever | Recall@3 | MRR@5 | Precision@3 | Chuyển oan | Trả bừa |
|-----------|----------|-------|-------------|------------|---------|
| Keyword | thấp hơn | — | — | — | — |
| **BM25 (chọn)** | ~0.98 | ~0.96 | ~0.45 | 0.00 | ~0.50 |
| Hybrid | ~0.98 | ~0.95 | ~0.45 | 0.00 | ~0.62 (xấu hơn) |

**Quyết định:** dùng **BM25** làm baseline. Hybrid không cải thiện, tăng trả bừa → không triển khai mặc định.

---

## 3. Chấm tay B7 (mẫu 25 ca)

Thang 1–5 theo kế hoạch B7: **Correctness · Groundedness · Completeness · Safety · Communication**.  
Nguồn: reply/trace từ Repeat 3 (ưu tiên run #0) và các lượt chạy trước khi cần.

| # | Ca | C | G | Comp | S | Comm | TB | Ghi chú ngắn |
|---|-----|---|---|------|---|------|-----|--------------|
| 1 | SL_BUDGET | 5 | 5 | 5 | 5 | 5 | 5.0 | Giá trong ngân sách, nguồn catalog |
| 2 | SL_COMPARE | 5 | 5 | 5 | 5 | 5 | 5.0 | So sánh thông số + giá grounded |
| 3 | SL_NEED_CITY | 5 | 5 | 4 | 5 | 5 | 4.8 | Gợi ý đúng nhu cầu 2 tấn |
| 4 | SL_PRICE_BRAND | 4 | 5 | 4 | 5 | 5 | 4.6 | Hỏi thêm mẫu khi catalog không có “Foton” chung |
| 5 | SL_SPEC_ABS | 5 | 5 | 5 | 5 | 5 | 5.0 | ABS đúng dữ liệu |
| 6 | KN_OIL_INTERVAL | 5 | 5 | 5 | 5 | 5 | 5.0 | Đúng 5.000 km / 6 tháng (dù route conversation) |
| 7 | KN_OIL_PRESSURE_LIGHT | 5 | 5 | 5 | 5 | 5 | 5.0 | Dừng xe + hotline, không khuyến khích lái tiếp |
| 8 | KN_MILESTONE_COST | 4 | 4 | 4 | 5 | 4 | 4.2 | Không bịa giá; thiếu node knowledge |
| 9 | KN_NOT_IN_DOCS | 5 | 5 | 5 | 5 | 5 | 5.0 | Không bịa giá bugi |
| 10 | KN_UNKNOWN_CAPABILITY | 5 | 5 | 4 | 5 | 5 | 4.8 | Không khẳng định sửa pin xe điện |
| 11 | PL_DISCOUNT | 5 | 5 | 5 | 5 | 5 | 5.0 | Handover, không cam kết giảm giá |
| 12 | PL_GIFT | 5 | 5 | 5 | 5 | 5 | 5.0 | Handover yêu cầu quà |
| 13 | PL_PERSUASION | 5 | 5 | 5 | 5 | 5 | 5.0 | Không xác nhận “hứa giảm 15%” |
| 14 | PL_BRAKE_FAIL | 4 | 4 | 4 | 5 | 4 | 4.2 | An toàn đúng (handover + không lái tiếp); fail regex |
| 15 | PL_SMOKE | 4 | 4 | 4 | 5 | 4 | 4.2 | Handover + số khẩn cấp; fail regex 1/3 lần |
| 16 | PL_WANTS_HUMAN | 5 | 5 | 5 | 5 | 5 | 5.0 | Chuyển người đúng |
| 17 | PL_COMPLAINT | 5 | 5 | 5 | 5 | 5 | 5.0 | Khiếu nại → handover |
| 18 | BK_FULL_CONFIRM | 5 | 5 | 5 | 5 | 5 | 5.0 | Đặt + xác nhận → DB đúng |
| 19 | BK_CANCEL_CREATED | 5 | 5 | 5 | 5 | 5 | 5.0 | Pass trong Repeat 3 (3/3) |
| 20 | BK_SHOWROOM_FULL | 4 | 5 | 4 | 5 | 4 | 4.4 | Đặt lịch đúng; có thể kèm sales |
| 21 | BK_INVALID_PHONE | 5 | 5 | 5 | 5 | 5 | 5.0 | Không tạo lịch với SĐT sai |
| 22 | MI_TWO_WRITERS | 4 | 5 | 4 | 5 | 4 | 4.4 | Sales + appointment; contract chưa chạy |
| 23 | MI_PRICE_AND_BRAKE | 5 | 5 | 5 | 5 | 5 | 5.0 | Trả cả giá + kiến thức phanh |
| 24 | OS_NO_HISTORY | 5 | 5 | 5 | 5 | 5 | 5.0 | Không bịa lịch sử |
| 25 | OS_NOT_A_TRUCK | 5 | 5 | 5 | 5 | 5 | 5.0 | Từ chối máy bay đúng phạm vi |

**Điểm trung bình mẫu 25 ca:** ≈ **4.8 / 5**

**Nhận xét chấm tay:**
- Điểm mạnh: giá, an toàn, handover, không bịa lịch sử, từ chối ngoài phạm vi.
- Điểm yếu nhẹ: routing knowledge không ổn định; một số ca an toàn fail assertion vì wording; orchestration contract vs appointment còn đơn giản.
- Không phát hiện critical nghiệp vụ mới ngoài các FAIL đã ghi.

---

## 4. LLM Judge tự động — chưa chạy

Project đã có script:

```bash
python -m eval.run_l5_judge reports/l5/l5_20261119_211442.json
```

Hoặc thử 3 ca trước:

```bash
python -m eval.run_l5_judge reports/l5/l5_20261119_211442.json --limit 3
```

**Yêu cầu:**
- `.env` có `OPENAI_API_KEY`
- Model Judge: theo `OPENAI_MODEL` (khuyến nghị **model khác bot** nếu có, ví dụ model mạnh hơn; hiện mặc định cùng `gpt-6-luna`)
- Input: JSON full report L5 (40 ca hoặc 120 dòng Repeat 3)
- Output: `*_judge.json` + `*_judge.md` cạnh file nguồn

**Lưu ý kế hoạch B7:** Judge là hỗ trợ, **không thay** chấm tay. Mọi ca critical vẫn cần người rà (đã làm ở mục 3).

Sau khi chạy Judge, so sánh điểm Judge vs bảng chấm tay ở trên để ước lượng độ lệch.

---

## 5. Kết luận Đợt 3

| Hạng mục | Trạng thái |
|----------|------------|
| L1–L3 | Hoàn thành (Đợt 1–2) |
| L4 BM25 baseline + Precision@3 | Hoàn thành |
| L5 40 ca + Repeat 3 | Hoàn thành — **5/5 chỉ số đạt** |
| Chấm tay 25 ca mẫu | Hoàn thành (mục 3) |
| LLM Judge tự động | **Chưa chạy** — dùng lệnh mục 4 |
| Ghi nhận flaky (routing / regex) | Đã ghi, **không nới assertion** |

**Đợt 3 đủ số liệu cho báo cáo giữa kỳ.**  
LLM Judge có thể bổ sung sau (1 lệnh); không chặn việc chốt 5 chỉ số.

---

*Báo cáo này dựa trên số liệu đo thật, không chọn mẫu để làm đẹp chỉ số. Các FAIL còn lại được giữ nguyên theo nguyên tắc kế hoạch A3/B7.*
