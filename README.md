# Hướng dẫn cài đặt

## Yêu cầu

- Python 3.10 trở lên (kiểm tra: `python3 --version`)
- Git

## Các bước

**1. Lấy mã nguồn và vào thư mục gốc của project**

```bash
git clone <địa-chỉ-repo>
cd Personalized-Chatbot-for-auto-request-response-supporting-automobile-garage-management
```

**2. Tạo môi trường ảo**

```bash
python3 -m venv .venv
```

**3. Kích hoạt môi trường ảo**

```bash
# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

**4. Cài thư viện**

```bash
pip install -r requirements.txt
```

**5. (Tùy chọn) Tạo file cấu hình**

```bash
cp .env.example .env
```

Chỉ cần khi muốn đổi cấu hình (database, API key LLM...). Không có file này, hệ thống vẫn chạy bằng giá trị mặc định.

**6. Chạy server**

```bash
python -m uvicorn app.main:app --reload
```
hoặc
```bash
python run.py
```

Mở trình duyệt tại http://localhost:8000.

> Luôn chạy lệnh từ thư mục gốc `xuongxe-agent/`. Không chạy `python app/main.py`, vì sẽ báo lỗi `No module named 'app'`.

## Kiểm tra cài đặt thành công

```bash
pytest
python -m eval.run_eval
```

`pytest` báo `passed` và `run_eval` báo `6/6 passed` là cài đặt đúng.

## Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `No module named 'app'` | Đang chạy sai thư mục hoặc sai lệnh. Quay về thư mục gốc và dùng lệnh ở bước 6. |
| `No module named 'fastapi'` (hoặc thư viện khác) | Chưa kích hoạt môi trường ảo (bước 3) hoặc chưa cài thư viện (bước 4). |
| `ensurepip is not available` khi tạo venv (Debian/Ubuntu) | `sudo apt install python3-venv` |
| `Address already in use` | Cổng 8000 đang bị chiếm. Thêm `--port 8001` vào lệnh chạy. |
