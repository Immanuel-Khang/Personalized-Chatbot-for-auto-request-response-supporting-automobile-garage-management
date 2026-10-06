# Danh sách yêu cầu (điền 17 yêu cầu đã chốt vào đây)

Mỗi yêu cầu viết dạng: *"Là [vai], tôi muốn [làm gì], để [mục đích]"* + tiêu chí hoàn thành đo được
(ưu tiên dùng chính eval case trong `eval/cases.json`).

| ID | User story | Mức (Must/Should/Could) | Mức làm (thật/demo/bỏ) | Track | Eval case | Trạng thái |
|---|---|---|---|---|---|---|
| R01 | Là khách, tôi muốn hỏi giá xe qua chat để biết xe nào hợp ngân sách | Must | thật | B | `price` | ✅ khung |
| R02 | Là khách, tôi muốn đặt lịch bảo dưỡng qua chat | Must | thật | B+C | `appointment_multi_turn` | ✅ khung |
| R03 | Là khách, tôi muốn hỏi kiến thức bảo dưỡng | Must | thật | A+B | `knowledge` | ✅ khung |
| R04 | Là xưởng, tôi muốn bot không tự hứa giảm giá mà chuyển cho nhân viên | Must | thật | B | `negotiation` | ✅ khung |
| R05 | ... | | | | | |
