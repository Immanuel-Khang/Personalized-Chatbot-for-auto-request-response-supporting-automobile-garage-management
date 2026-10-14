# Danh sách yêu cầu (điền 17 yêu cầu đã chốt vào đây)

Mỗi yêu cầu viết dạng: *"Là [vai], tôi muốn [làm gì], để [mục đích]"* + tiêu chí hoàn thành đo được
(ưu tiên dùng chính eval case trong `eval/cases.json`).

| ID | User story | Mức (Must/Should/Could) | Mức làm (thật/demo/bỏ) | Track | Eval case | Trạng thái |
|---|---|---|---|---|---|---|
| R01 | Là khách, tôi muốn hỏi giá xe qua chat để biết xe nào hợp ngân sách | Must | thật | B | `price` | ✅ khung |
| R02 | Là khách, tôi muốn đặt lịch bảo dưỡng qua chat | Must | thật | B+C | `appointment_multi_turn` | ✅ khung |
| R03 | Là khách, tôi muốn hỏi kiến thức bảo dưỡng | Must | thật | A+B | `knowledge` | ✅ khung |
| R04 | Là xưởng, tôi muốn bot không tự hứa giảm giá mà chuyển cho nhân viên | Must | thật | B | `negotiation` | ✅ khung |
| R05 | Là khách, tôi muốn hỏi nhiều ý trong một tin (giá, kỹ thuật, đặt lịch) và được trả lời đủ trong một lượt | Must | thật | B | `sales_and_booking_same_turn`, `knowledge_and_booking_same_turn`, `parallel_read_agents`, `two_writers_deferred` | ✅ |
| R06 | Là nhân viên, tôi muốn tiếp nhận toàn bộ tin khách mà bot không chen vào sau handover | Must | thật | B+C | `human_pending_bypass` | ✅ |
| R07 | Là khách, tôi muốn hỏi lịch sử dịch vụ mà bot không tạo dữ liệu giả | Should | demo (service stub, DB P1) | A+B | `after_sales_history` | ✅ stub |
| R08 | Là khách, tôi muốn hủy yêu cầu chờ hoặc đổi chủ đề mà không bị ép đặt lịch | Must | thật | B | `booking_cancel`, `showroom_visit` | ✅ |
