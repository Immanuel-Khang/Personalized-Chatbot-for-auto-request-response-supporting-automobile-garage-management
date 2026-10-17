import os

# Phải đặt TRƯỚC khi import app. Test chạy trên DB Postgres riêng, bị xóa sạch mỗi lần chạy.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://garage:garage@localhost:5432/garage_test")
os.environ["LLM_PROVIDER"] = "none"  # test chạy rule-based, không cần API key

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import engine, Base

assert engine.url.database.endswith("_test"), "Test chỉ được chạy trên DB có tên *_test"

# Drop and recreate schema
with engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))

# Create all tables from models
Base.metadata.create_all(bind=engine)

from app.main import app  # noqa: E402
from app.db.session import SessionLocal


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    """Provide a database session for tests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
