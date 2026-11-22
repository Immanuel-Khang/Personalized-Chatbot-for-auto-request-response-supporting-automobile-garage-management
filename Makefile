.PHONY: dev prod down logs clean migrate seed test

# Chạy môi trường dev (build lại image và hiển thị log trực tiếp)
dev:
	docker compose -f docker-compose.dev.yml up --build -d
	@echo "Waiting for database to be ready..."
	@sleep 3
	@echo "Running migrations..."
	@source .venv/bin/activate && alembic upgrade head
	@echo "Development environment ready!"
	docker compose -f docker-compose.dev.yml logs -f

# Chạy môi trường prod (chạy ngầm ở chế độ daemon -d)
prod:
	docker compose -f docker-compose.prod.yml up -d --build

# Dừng toàn bộ containers
down:
	docker compose -f docker-compose.dev.yml down
	docker compose -f docker-compose.prod.yml down

# Xem log thời gian thực của prod
logs:
	docker compose -f docker-compose.prod.yml logs -f

# Dọn dẹp sạch sẽ cả volume dữ liệu (nếu muốn reset tinh tươm)
clean:
	docker compose -f docker-compose.dev.yml down -v
	docker compose -f docker-compose.prod.yml down -v

# Đưa danh mục xe + chỉ mục RAG về đúng dữ liệu chuẩn (data/catalog_master_refined.csv). Mọi người chạy lệnh này để DB giống nhau.
seed:
	docker compose -f docker-compose.dev.yml exec web python -m app.manage bootstrap-catalog

# Run the test suite (needs Postgres on 127.0.0.1:5433)
test:
	LLM_PROVIDER=none pytest tests/ -q
