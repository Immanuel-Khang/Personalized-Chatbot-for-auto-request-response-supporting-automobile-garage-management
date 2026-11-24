"""Đợt 3B: khởi động app dựng sẵn graph + bảng checkpoint (sửa lỗi treo yêu cầu chat đầu tiên). ID: ST01-ST02.

Lỗi gốc (quan sát khi chạy pytest trên DB trống): handle_message gọi memory_checkpoint() khi đang mở một phiên DB;
lần gọi đầu dựng graph và LangGraph chạy `CREATE INDEX CONCURRENTLY` trên bảng checkpoint, lệnh này chờ mọi
transaction cũ kết thúc, trong đó có chính phiên DB đang mở -> treo vĩnh viễn. Cách sửa: lifespan gọi get_graph() trước
khi nhận request.
"""
from fastapi.testclient import TestClient

import app.agent.graph as graph_module
from app.main import app


def test_st01_lifespan_builds_the_graph_before_serving(monkeypatch):
    built = []
    real = graph_module.get_graph
    monkeypatch.setattr(graph_module, "get_graph", lambda: built.append(True) or real())
    with TestClient(app):
        assert built, "lifespan phải gọi get_graph() để checkpoint được dựng trước request đầu tiên"


def test_st02_graph_is_cached_after_startup(client):
    assert graph_module.get_graph.cache_info().currsize == 1
