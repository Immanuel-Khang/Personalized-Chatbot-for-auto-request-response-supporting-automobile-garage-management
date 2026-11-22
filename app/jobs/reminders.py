"""[TRACK C - P1] Job nhắc bảo dưỡng.
TODO[JOBS]: dùng APScheduler. Quy trình: quét xe tới hạn -> kiểm tra consent -> ghi vào bảng outbox -> worker gửi.
Lúc demo có thể chạy tay: python -m app.jobs.reminders"""
