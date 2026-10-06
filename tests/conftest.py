import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"  # phải đặt TRƯỚC khi import app
os.environ["LLM_PROVIDER"] = "none"  # test chạy rule-based, không cần API key
if os.path.exists("test.db"):
    os.remove("test.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
