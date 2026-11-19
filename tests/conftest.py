import os

# Phải đặt TRƯỚC khi import app. Test chạy trên DB Postgres riêng, bị xóa sạch mỗi lần chạy.
# Dùng 127.0.0.1 thay vì localhost: localhost thử IPv6 (::1) trước và chờ hết timeout mới sang IPv4.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://garage:garage@127.0.0.1:5433/garage_test?connect_timeout=5")
# Mặc định test KHÔNG gọi LLM thật (nhanh, xác định, không tốn tiền) dù .env có LLM_PROVIDER=openai.
# Muốn chạy test với LLM thật (tầng L5): RUN_LLM_TESTS=1 pytest <file> (dùng ở Đợt 3)
if os.getenv("RUN_LLM_TESTS") != "1":
    os.environ["LLM_PROVIDER"] = "none"
# RAG chạy BM25-only: không gọi OpenAI embeddings (mỗi câu hỏi 1 request mạng, mạng chậm -> treo cả suite)
os.environ["EMBEDDING_PROVIDER"] = "none"
os.environ["ADMIN_AUTH_REQUIRED"] = "false"  # test admin API không đăng nhập

# Temporary skip list: conversation tests that need an LLM/FakeLLM (see tests/needs_llm.txt).
NEEDS_LLM = set()
_needs_llm_file = os.path.join(os.path.dirname(__file__), "needs_llm.txt")
if os.path.exists(_needs_llm_file):
    with open(_needs_llm_file, encoding="utf-8") as _fh:
        NEEDS_LLM = {line.strip() for line in _fh if line.strip() and not line.startswith("#")}

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
from app.db.session import engine, SessionLocal, Base, init_db  # noqa: E402
from app.db import models  # noqa: E402, F401 - Import to register models

# Reset schema mỗi lần chạy rồi dựng bằng Alembic (giống production): lifespan của app gọi
# `alembic upgrade head` lần nữa sẽ là no-op. create_all không ghi alembic_version -> CREATE TABLE trùng.
with engine.begin() as conn:
    assert engine.url.database and engine.url.database.endswith("_test"), \
    f"Từ chối DROP SCHEMA trên DB {engine.url.database!r}"
    conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
init_db()


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


def pytest_collection_modifyitems(config, items):
    from app.agent.llm import _get_llm
    if _get_llm() is not None:
        return
    skip = pytest.mark.skip(reason="Needs LLM/FakeLLM: preprocess has no rule-based branch (see tests/needs_llm.txt)")
    for item in items:
        if item.nodeid in NEEDS_LLM:
            item.add_marker(skip)


# Fixture FakeLLM (Đợt 2): thay LLM bằng kịch bản; tool_call_spy ghi công cụ nào được chạy ở node nào.
from tests.fake_llm import fake_llm, tool_call_spy  # noqa: E402, F401
