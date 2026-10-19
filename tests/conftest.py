import os

# Phải đặt TRƯỚC khi import app. Test chạy trên DB Postgres riêng, bị xóa sạch mỗi lần chạy.
# Dùng 127.0.0.1 thay vì localhost: localhost thử IPv6 (::1) trước và chờ hết timeout mới sang IPv4.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://garage:garage@127.0.0.1:5432/garage_test?connect_timeout=5")
os.environ["LLM_PROVIDER"] = "none"  # test chạy rule-based, không cần API key

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_URL = make_url(os.environ["DATABASE_URL"])
assert TEST_URL.database.endswith("_test"), "Test chỉ được chạy trên DB có tên *_test"


def _ensure_test_database() -> None:
    """Tạo DB *_test nếu chưa có (kết nối qua DB 'postgres'); lỗi kết nối báo rõ thay vì treo."""
    admin = create_engine(TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": TEST_URL.database}).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_URL.database}"'))
    except Exception as e:
        pytest.exit(f"Không kết nối được Postgres tại {TEST_URL.host}:{TEST_URL.port} "
                    f"(đã bật container DB chưa?): {e}", returncode=2)
    finally:
        admin.dispose()


_ensure_test_database()

from app.main import app  # noqa: E402
from app.db.session import engine, SessionLocal, Base  # noqa: E402
from app.db import models  # noqa: E402, F401 - Import to register models

# Reset schema mỗi lần chạy: bảng sót lại từ lần chạy trước (bị ngắt giữa chừng) không làm hỏng create_all
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
Base.metadata.create_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    """Provide a clean database session for each test."""
    db = SessionLocal()
    try:
        # Clean all tables before test
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
        yield db
    finally:
        db.close()
