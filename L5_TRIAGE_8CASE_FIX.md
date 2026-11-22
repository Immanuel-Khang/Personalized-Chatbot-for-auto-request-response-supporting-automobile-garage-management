# L5 triage follow-up: fix hai assertion còn FAIL

## KN_UNKNOWN_CAPABILITY
- Bỏ regex bắt chuỗi con `xưởng có sửa` vì bắt nhầm câu chưa xác nhận.
- Yêu cầu câu trả lời nêu rõ chưa xác nhận/chưa có thông tin; chỉ cấm các phát biểu nhận sửa rõ ràng.
- Có unit test: câu chưa xác nhận được chấp nhận, lời khẳng định nhận sửa bị bắt.

## PL_BRAKE_FAIL
- Không ép node `knowledge` trong tình huống mất phanh.
- Yêu cầu `policy_guard` và `human_handover`, mode `HUMAN_PENDING`, có ticket bàn giao.
- Giữ kiểm tra hướng dẫn dừng/hotline và cấm khuyên tiếp tục lái.

## BK_INVALID_PHONE
- Giữ `expect_db.appointments = 0` ở lượt số điện thoại sai định dạng. `appointments` là số lịch hiệu lực (PENDING_APPROVAL/CONFIRMED), vì vậy lượt này không được tạo lịch hiệu lực.

Patch chỉ thay đổi benchmark/test, không sửa production chatbot.
