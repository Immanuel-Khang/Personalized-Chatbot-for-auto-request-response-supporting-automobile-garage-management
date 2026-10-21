# Tổng hợp thay đổi: hội thoại tự nhiên cho chatbot xưởng

Nhánh: `feat/conversation_agent`. Chưa commit, chưa push.

Kết quả test: **134 passed, 4 xfailed** (trước khi sửa: 110 passed, 16 xfailed).

## 1. Nguyên nhân gốc

| Vấn đề | Vị trí | Hậu quả |
|---|---|---|
| Slot đặt lịch bị ghi từ mọi tin nhắn | `preprocess.py` (`_rule_based_preprocess`, `_llm_preprocess`) | "có sửa kính không?" ghi `service_type=REPAIR`, câu hỏi bị coi như bắt đầu đặt lịch (DEFECT-02) |
| Regex thời gian chỉ bắt ngày và buổi | `preprocess.py` | Mất "10 giờ"; "10 giờ sáng nhé" không được nhận là trả lời slot, router trả "chưa hiểu" (DEFECT-05, 06) |
| Câu hỏi dịch vụ không khớp intent nào | `router.py` → `conversation.py` | Rơi vào câu trả lời chung "chưa hiểu ý bạn", không trả lời câu hỏi (DEFECT-01, 04) |
| Thiếu từ khóa | `preprocess.KEYWORDS` | "giảm 15%" không bị nhận là mặc cả; "muốn mua / tìm một chiếc" không vào sales (DEFECT-08, 09) |
| Hỏi thủ tục hợp đồng mở luôn contract flow | `router._groups` → `contract.py` | Hỏi giấy tờ thì bot xin SĐT, địa chỉ, giá chốt (DEFECT-10) |
| Không phân biệt gợi ý và thực hiện | `appointment.py` | Đủ slot là tạo lịch ngay dù khách chưa đồng ý; lỗi từ BookingService làm crash lượt chat |

## 2. Thay đổi theo file

### `app/agent/nodes/preprocess.py`
- Chỉ ghi slot đặt lịch (`service_type`, `appointment_when`, `service_detail`) khi có ngữ cảnh đặt lịch: intent APPOINTMENT, flow appointment đang dở, appointment đang hoãn (`pending_intent`), hoặc bot vừa gợi ý đặt lịch (`offer`). Ngoài ngữ cảnh đó thì giữ nguyên giá trị cũ (`_booking_context`, `_keep_old_booking_slots`).
- Thêm `extract_when()`: ghép ngày + giờ + buổi; chỉ có giờ thì lấy ngày của lượt trước ("Khoảng 10 giờ sáng" sau "ngày mai" → "ngày mai 10 giờ sáng").
- Nhận "giảm N%" là DISCOUNT (`DISCOUNT_PERCENT`).
- Thêm từ khóa SALES: "muốn mua", "tìm mua", "tìm một chiếc", "tìm xe".
- "chưa định đặt lịch" / "không muốn đặt lịch" không còn là APPOINTMENT (`NEGATED_BOOKING`).
- Slot mới `service_detail` ghi bộ phận khách nhắc tới, để theo dõi khi khách đổi hạng mục (kính → điều hòa).

### `app/agent/state.py`
- Thêm một trường `offer`: bước bot vừa gợi ý nhưng chưa thực hiện. Chỉ router ghi, router xóa mỗi lượt, nên chỉ có hiệu lực đúng một lượt.

### `app/agent/nodes/router.py`
- Hỏi dịch vụ khi không có flow nào đang dở → chuyển sang conversation agent với lý do `service_inquiry` và tạo `offer` đặt lịch, **không** mở flow.
- Khách nhận lời gợi ý (đồng ý hoặc trả lời ngày giờ) → vào appointment. Trả lời khác → bỏ offer và bỏ slot đặt lịch vừa lấy theo ngữ cảnh gợi ý.
- Hỏi giấy tờ/thủ tục (`CONTRACT_QUESTION`) khi chưa trong contract flow → trả lời như câu hỏi (`contract_inquiry`), không mở contract flow.
- `BOOKING_SLOTS` thêm `service_detail` để hủy đặt lịch xóa luôn trường này.

### `app/agent/nodes/conversation.py`
- Thêm hai loại trả lời `service_inquiry` và `contract_inquiry`: lấy tài liệu RAG làm nguồn duy nhất, không khẳng định năng lực khi tài liệu không nói rõ, không xin SĐT, không chẩn đoán chắc chắn, không bịa giá hoặc danh mục giấy tờ.
- Fallback khi không có LLM: nói kỹ thuật viên cần xem trực tiếp và gợi ý sắp xếp mang xe qua.

### `app/agent/nodes/appointment.py`
- Khách mới chỉ nhận lời gợi ý của bot (chưa nói muốn đặt) → hỏi xác nhận trước khi tạo lịch.
- BookingService ném lỗi hoặc trả thất bại → trả lời trung thực ("chưa xác nhận được" + hotline), không lộ lỗi nội bộ, giữ slot để thử lại. SĐT không hợp lệ vẫn hỏi lại SĐT như cũ.
- Đặt lịch thành công chỉ xóa slot đặt lịch, giữ tên, SĐT, dòng xe.

### `app/agent/nodes/service_type.py`
- REPAIR khớp thêm "nứt", "vỡ", "kiểm tra".
- Thêm `detect_component()` và `is_service_inquiry()`.

### `app/agent/nodes/common.py`
- `STYLE_RULES` thành chính sách hội thoại dùng chung cho mọi prompt (một chỗ, không lặp trong từng prompt).

## 3. Chính sách hội thoại

1. Trả lời thẳng câu khách hỏi trước, sau đó gợi ý tối đa một bước tiếp theo hữu ích. Không bắt câu nào cũng kết bằng câu hỏi.
2. Chỉ hỏi thông tin cần cho việc đang làm; không hỏi lại thông tin đã có.
3. Gợi ý không phải là thực hiện: chỉ vào flow đặt lịch khi khách muốn đặt rõ ràng hoặc nhận lời gợi ý; chỉ nói "đã ghi nhận" khi BookingService báo thành công.
4. Chỉ dùng catalog, tài liệu và kết quả service làm sự thật; thiếu dữ liệu thì nói rõ phần nào cần nhân viên xác nhận.
5. Giảm giá vẫn qua `policy_guard` chuyển người duyệt; `output_guard` vẫn chặn lời hứa. Không nới lỏng ràng buộc nào.

## 4. Test

### Bỏ đánh dấu xfail (lỗi đã sửa, giữ nguyên assertion)
DEFECT-01, 02, 04, 05, 06, 08, 09, 10 trong `test_conv_booking.py`, `test_conv_context.py`, `test_conv_inquiry.py`, `test_conv_sales.py`.

### Sửa một test
`test_tc09_generated_discount_promise_is_blocked` trước dùng câu "hứa giảm 15%". Câu này giờ bị `policy_guard` chặn sớm nên test không còn kiểm tra `output_guard`. Đổi sang câu không có từ khóa giảm giá để test vẫn kiểm tra riêng `output_guard`.

### File mới `tests/test_conv_policy.py` (12 test)
- `test_service_inquiry_leaves_offer_not_flow`: hỏi dịch vụ chỉ tạo offer, không mở flow, không xin SĐT.
- `test_accepting_offer_with_time_enters_booking_without_creating`: trả lời giờ sau gợi ý → vào flow, hỏi SĐT, chưa tạo lịch.
- `test_known_phone_offer_acceptance_asks_confirmation_before_booking`: giờ dự kiến chưa tạo lịch; "Được" mới tạo.
- `test_ignored_offer_expires_and_keeps_no_booking_slot`: không nhận gợi ý → offer hết hạn, không còn slot đặt lịch.
- `test_tc14_price_only_does_not_start_booking`
- `test_tc15_switching_service_updates_context_keeps_identity`
- `test_tc16_booking_error_is_not_reported_as_booked`
- `test_tc12_price_interrupt_keeps_pending_booking`
- `test_tc09_purchase_discount_goes_to_manager`
- `test_tc06_short_time_reply_fills_slot`
- `test_extract_when_merges_hour_with_previous_day` (đơn vị)
- `test_router_always_clears_offer` (đơn vị)

### Lệnh đã chạy
```bash
podman start garage-pg            # Postgres test chưa chạy, khởi động container có sẵn
.venv/bin/python -m pytest tests/ -q
# 134 passed, 4 xfailed
```

## 5. Hạn chế còn lại

- Chưa có service kiểm tra lịch trống: `DbBookingService` chỉ ghi lịch PENDING_APPROVAL với thời gian dạng văn bản, chưa quy đổi ngày giờ theo múi giờ. Bot báo đúng trạng thái chờ duyệt nhưng chưa đề xuất được khung giờ thật.
- Toàn bộ test chạy rule-based (`LLM_PROVIDER=none`). Thay đổi prompt và phân loại bằng LLM chưa được kiểm thử với model thật; repo chưa có tầng test LLM riêng.
- Còn xfail: DEFECT-03 (hỏi giá kính trả giá lọc gió), DEFECT-07 (chưa hủy được lịch đã tạo), DEFECT-11 (trả lời chẩn đoán lượt 2 lặp lượt 1), DEFECT-12 (hỏi giá thay dầu trả giá xe).
- Nhận diện câu hỏi hợp đồng dựa trên danh sách từ khóa ngắn; `appointment.py` import `_affirm` từ router.

## 6. Rà soát diff

- Code: `appointment.py`, `common.py`, `conversation.py`, `preprocess.py`, `router.py`, `service_type.py`, `state.py`.
- Test: sửa 4 file `test_conv_*.py`, thêm `test_conv_policy.py`.
- Không đổi schema, migration, `.env` hay dependency.
- Thay đổi chưa commit có sẵn của bạn trong `app/db/session.py` (xóa một dòng trống) được giữ nguyên.
