# Đợt 3 — L4 mở rộng + LLM Judge

## A. L4 mở rộng (60 câu)

Patch này thay `eval/rag_cases.json` (26 → 60 câu) và `eval/run_rag_eval.py` (thêm Precision@3). Precision@3 dùng mẫu số cố định 3: nếu retriever trả ít hơn 3 chunk, vị trí thiếu tính không relevant. Một chunk relevant khi chứa toàn bộ `gold_contains` sau chuẩn hóa không dấu/không phân biệt hoa thường. Recall@k và MRR@5 giữ định nghĩa hiện có.

Chạy tại thư mục gốc project:

```bash
python -m eval.run_rag_eval --configs keyword,bm25 -v
python -m eval.run_rag_eval --configs keyword,bm25,hybrid -v
```

- Lệnh 1 kiểm tra keyword/BM25, không cần embedding API.
- Lệnh 2 cần `EMBEDDING_PROVIDER=openai` và `OPENAI_API_KEY` hợp lệ; hybrid có thể cần DB/pgvector theo cấu hình hiện tại.
- Lưu output console thành báo cáo riêng, không chỉnh ngưỡng/trọng số trước khi lưu baseline.
- Ghi cả Recall@1/3/5, MRR@5, Precision@3, false handover, false answer và kết quả theo nhóm.
- Bộ 60 câu có 52 câu in-scope và 8 câu out-of-scope. Kiểm tra lại nội dung/gold nếu sửa tài liệu knowledge sau này.

## B. LLM Judge cho kết quả L5

Script chỉ chấm lại nội dung đã có trong JSON report; KHÔNG gọi lại chatbot. Dùng `OPENAI_API_KEY` và `OPENAI_MODEL` trong `.env`, mặc định model Judge theo `OPENAI_MODEL` (mặc định `gpt-6-luna`).

```bash
python -m eval.run_l5_judge reports/l5/l5_YYYYMMDD_HHMMSS.json
```

Thay tên bằng **JSON full report 40 ca** được sinh ở lần chạy L5 full. Không dùng JSON của lượt `-k PL_SMOKE` vì nó chỉ có 1 ca. Nếu muốn thử cấu hình/chi phí trên một phần nhỏ trước:

```bash
python -m eval.run_l5_judge reports/l5/l5_YYYYMMDD_HHMMSS.json --limit 3
```

Mỗi case được gửi thành một request Judge (bao gồm các lượt hội thoại của case), output tạo cạnh file nguồn:
- `*_judge.json`: điểm và lý do từng lượt.
- `*_judge.md`: bảng tổng hợp.

Năm tiêu chí 0–2: correctness, intent, completeness, safety, tool_use (tổng 10). Judge cũng ghi grounding = supported/unsupported/unclear. **L5 JSON không chứa retrieved passages**, do đó grounding thường phải là `unclear`; không dùng Judge để khẳng định hallucination nếu thiếu nguồn tri thức. Judge là đánh giá hỗ trợ, không thay thế chấm tay B7. Phải rà thủ công mọi ca critical và hai ca booking L5 hiện còn lỗi.

## C. Trạng thái/điều kiện

Theo log mới nhất, L5 full run là 38/40, còn `BK_CANCEL_CREATED` (DB trạng thái hủy) và `BK_SHOWROOM_FULL` (trace có node sales ngoài mong đợi) FAIL. `PL_SMOKE` đã PASS riêng. Không ghi L5 là 40/40 PASS cho đến khi hai lỗi booking được triage và xác minh. Có thể tiến hành L4 và Judge song song, nhưng cần ghi rõ trạng thái L5 này trong báo cáo B7.
