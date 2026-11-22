FROM python:3.12-slim

# Ngăn Python tạo file .pyc và bật unbuffered log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Cài đặt các công cụ hệ thống cần thiết (nếu có thư viện C cần compile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy và cài đặt dependencies trước (để tận dụng Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn vào container
COPY . .

# Mở port 8000
EXPOSE 8000

# Lệnh mặc định: chạy migration và khởi động server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
