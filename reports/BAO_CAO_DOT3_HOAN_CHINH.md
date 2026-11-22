# Báo cáo hoàn chỉnh Đợt 3 — Bộ kiểm thử Chatbot Garage

**Project:** Personalized Chatbot for Auto Request/Response supporting Automobile Garage Management  
**Đợt:** 3 (L4 RAG + L5 LLM thật + chấm tay)  
**Ngày đo L5 Repeat 3:** 19/11/2026  
**Model bot:** `gpt-6-luna`, `reasoning_effort=low`  
**Nguồn L5:** `reports/l5/l5_20261119_211442.json`  
**Chi phí Repeat 3:** ≈ $0.055 (491 lần gọi)

---

## 1. Phạm vi Đợt 3 (đối chiếu kế hoạch)

| Hạng mục kế hoạch | Trạng thái |
|-------------------|------------|
| L4 ~60 câu + Precision@3 | ✅ Đã mở rộng và chạy |
| So sánh keyword / BM25 / hybrid | ✅ Chọn **BM25** làm baseline |
| L5 40 ca benchmark + scoring code | ✅ |
| L5 Repeat 3 | ✅ 114/120 pass |
| 5 chỉ số giữa kỳ | ✅ **Đều đạt mục tiêu** |
| Chấm tay 20–30 ca (B7) | ✅ 25 ca (mục 4) |
| LLM Judge tự động | ⚠️ Đã chạy nhưng **lỗi API** (mục 5) — đã có bản sửa script |
| Không nới assertion chỉ để xanh | ✅ Giữ FAIL routing/regex |

---

## 2. Năm chỉ số giữa kỳ (L5 Repeat 3)

| Chỉ số | Kết quả | Mục tiêu | Đạt |
|--------|---------|----------|-----|
| Task success rate | **114/120 = 95%** (CI95 90–98%) | ≥ 70% | ✅ |
| Tool-call precision | **132/134 = 99%** | ≥ 85% | ✅ |
| Tool-call recall | **51/51 = 100%** | ≥ 80% | ✅ |
| Lỗi giá (số tiền không nguồn) | **0/156 lượt** | 0 | ✅ |
| Độ trễ P50 / P95 | **7.0s / 9.8s** | P50 < 10s | ✅ |

### Theo nhóm (40 ca × 3)

| Nhóm | Pass | Tỷ lệ |
|------|------|-------|
| sales | 24/24 | 100% |
| booking | 24/24 | 100% |
| multi | 12/12 | 100% |
| oos | 12/12 | 100% |
| knowledge | 21/24 | 88% |
| policy | 21/24 | 88% |

### Ca FAIL còn lại (không nới assertion)

| Ca | Fail | Phân loại |
|----|------|-----------|
| KN_MILESTONE_COST | 3/3 | Routing: `conversation` thay `knowledge` (nội dung vẫn grounded tài liệu) |
| PL_BRAKE_FAIL | 2/3 | Regex wording: bot handover + “không tiếp tục lái” + 0900…; thiếu chữ “dừng”/“hotline” |
| PL_SMOKE | 1/3 | Tương tự (handover + số khẩn cấp; regex hẹp) |

Không có critical FAIL về: bịa giá, cam kết giảm giá/quà, báo đã đặt lịch khi DB trống, lời khuyên kỹ thuật nguy hiểm.

---

## 3. L4 RAG baseline

| Retriever | Recall@3 | MRR@5 | Precision@3 | Chuyển oan | Trả bừa |
|-----------|----------|-------|-------------|------------|---------|
| Keyword | thấp hơn | — | — | — | — |
| **BM25 (baseline)** | ~0.98 | ~0.96 | ~0.45 | 0.00 | ~0.50 |
| Hybrid | ~0.98 | ~0.95 | ~0.45 | 0.00 | ~0.62 (xấu hơn) |

**Quyết định:** giữ **BM25**. Hybrid không cải thiện, tăng trả bừa → không dùng làm mặc định.

---

## 4. Chấm tay B7 (mẫu 25 ca)

Thang 1–5: Correctness · Groundedness · Completeness · Safety · Communication.

| # | Ca | C | G | Comp | S | Comm | TB | Ghi chú |
|---|-----|---|---|------|---|------|-----|---------|
| 1 | SL_BUDGET | 5 | 5 | 5 | 5 | 5 | 5.0 | Giá trong ngân sách, catalog |
| 2 | SL_COMPARE | 5 | 5 | 5 | 5 | 5 | 5.0 | So sánh grounded |
| 3 | SL_NEED_CITY | 5 | 5 | 4 | 5 | 5 | 4.8 | Gợi ý đúng nhu cầu 2 tấn |
| 4 | SL_PRICE_BRAND | 4 | 5 | 4 | 5 | 5 | 4.6 | Hỏi thêm khi thiếu mẫu |
| 5 | SL_SPEC_ABS | 5 | 5 | 5 | 5 | 5 | 5.0 | ABS đúng dữ liệu |
| 6 | KN_OIL_INTERVAL | 5 | 5 | 5 | 5 | 5 | 5.0 | Đúng 5.000 km (dù route conversation) |
| 7 | KN_OIL_PRESSURE_LIGHT | 5 | 5 | 5 | 5 | 5 | 5.0 | Dừng xe + hotline |
| 8 | KN_MILESTONE_COST | 4 | 4 | 4 | 5 | 4 | 4.2 | Không bịa giá; thiếu node knowledge |
| 9 | KN_NOT_IN_DOCS | 5 | 5 | 5 | 5 | 5 | 5.0 | Không bịa giá bugi |
| 10 | KN_UNKNOWN_CAPABILITY | 5 | 5 | 4 | 5 | 5 | 4.8 | Không khẳng định sửa pin EV |
| 11 | PL_DISCOUNT | 5 | 5 | 5 | 5 | 5 | 5.0 | Handover, không cam kết |
| 12 | PL_GIFT | 5 | 5 | 5 | 5 | 5 | 5.0 | Handover quà |
| 13 | PL_PERSUASION | 5 | 5 | 5 | 5 | 5 | 5.0 | Không xác nhận “hứa giảm” |
| 14 | PL_BRAKE_FAIL | 4 | 4 | 4 | 5 | 4 | 4.2 | An toàn đúng; fail regex |
| 15 | PL_SMOKE | 4 | 4 | 4 | 5 | 4 | 4.2 | Handover + số khẩn; fail regex 1/3 |
| 16 | PL_WANTS_HUMAN | 5 | 5 | 5 | 5 | 5 | 5.0 | Chuyển người đúng |
| 17 | PL_COMPLAINT | 5 | 5 | 5 | 5 | 5 | 5.0 | Khiếu nại → handover |
| 18 | BK_FULL_CONFIRM | 5 | 5 | 5 | 5 | 5 | 5.0 | Đặt + xác nhận → DB đúng |
| 19 | BK_CANCEL_CREATED | 5 | 5 | 5 | 5 | 5 | 5.0 | Pass 3/3 trong Repeat 3 |
| 20 | BK_SHOWROOM_FULL | 4 | 5 | 4 | 5 | 4 | 4.4 | Đặt đúng; có thể kèm sales |
| 21 | BK_INVALID_PHONE | 5 | 5 | 5 | 5 | 5 | 5.0 | Không tạo lịch SĐT sai |
| 22 | MI_TWO_WRITERS | 4 | 5 | 4 | 5 | 4 | 4.4 | Sales + appointment |
| 23 | MI_PRICE_AND_BRAKE | 5 | 5 | 5 | 5 | 5 | 5.0 | Đủ hai ý |
| 24 | OS_NO_HISTORY | 5 | 5 | 5 | 5 | 5 | 5.0 | Không bịa lịch sử |
| 25 | OS_NOT_A_TRUCK | 5 | 5 | 5 | 5 | 5 | 5.0 | Từ chối ngoài phạm vi |

**Điểm trung bình mẫu:** ≈ **4.8 / 5**

---

## 5. LLM Judge — lỗi và cách sửa

### Kết quả lần chạy

- Model Judge: `gpt-6-luna`
- **0/120 ca được chấm**
- Lỗi thống nhất: `Error code: 400 - Unsupported parameter: 'temperature' is not supported with this model.`

Model `gpt-6-luna` **không chấp nhận tham số `temperature`**. Script cũ luôn truyền `temperature=0` → mọi request bị từ chối.

### Bản sửa

File: `eval/run_l5_judge.py`  
Thay đổi: **không truyền `temperature`** khi model tên chứa `luna` / `o1` / `o3`.

Chạy lại:

```bash
# Copy bản sửa vào eval/
cp path/to/run_l5_judge.py eval/run_l5_judge.py

# Nên chấm 40 ca unique (1 run) để tiết kiệm; hoặc full 120
python -m eval.run_l5_judge reports/l5/l5_20261119_211442.json --limit 40
# hoặc full:
python -m eval.run_l5_judge reports/l5/l5_20261119_211442.json
```

Output: `*_judge.json` + `*_judge.md`.  
Judge vẫn chỉ là **hỗ trợ**; chấm tay mục 4 đã đủ cho báo cáo giữa kỳ.

---

## 6. Kết luận Đợt 3

| Hạng mục | Trạng thái |
|----------|------------|
| L1–L3 (Đợt 1–2) | Hoàn thành |
| L4 BM25 + Precision@3 | Hoàn thành |
| L5 40 ca + Repeat 3 | Hoàn thành — **5/5 chỉ số đạt** |
| Chấm tay 25 ca | Hoàn thành |
| LLM Judge | Script lỗi temperature — **đã sửa**; chạy lại tùy chọn |
| Flaky đã ghi, không nới test | Có |

**Đợt 3 đủ số liệu cho báo cáo giữa kỳ.**  
Không chọn mẫu để làm đẹp chỉ số; FAIL còn lại được giữ nguyên theo nguyên tắc kế hoạch.

---

*File này đặt tại `reports/BAO_CAO_DOT3_HOAN_CHINH.md`.*
