"""Nạp danh mục xe từ data/catalog_master_refined.csv lần đầu chạy (DB chưa có sản phẩm nào), bật ACTIVE cho sản phẩm
đủ điều kiện (xem catalog_activate.ready_drafts) rồi dựng chỉ mục RAG sản phẩm. Sản phẩm còn vấn đề dữ liệu giữ DRAFT,
bot không thấy. data/catalog_master_refined.csv là file CSV DUY NHẤT được seed; muốn đổi dữ liệu thì sửa file này.
Người A thay file CSV bằng dữ liệu thật của xưởng; đổi SEED_PRICE_AS_OF khi giá trong file được cập nhật."""
from datetime import date
from pathlib import Path

from app.db.catalog_activate import activate
from app.db.catalog_import import import_csv
from app.db.models import CatalogProduct
from app.db.session import SessionLocal

DATA = Path(__file__).resolve().parents[2] / "data"
SEED_CSV = DATA / "catalog_master_refined.csv"
SEED_PRICE_AS_OF = date(2026, 10, 18)  # nguồn CSV không có ngày hiệu lực giá


def seed_catalog() -> None:
    with SessionLocal() as db:
        empty = db.query(CatalogProduct).count() == 0
    if empty:
        import_csv(SEED_CSV, SEED_PRICE_AS_OF)
        with SessionLocal() as db:
            activate(db)
            db.commit()
    # Luôn đồng bộ chỉ mục RAG (idempotent, gần như 0 chi phí khi không có gì đổi): máy mới, sau clean/import lại,
    # hay chưa từng chạy scripts/index_products.py đều tự có chỉ mục đúng.
    from app.rag.product_indexer import ensure_product_index
    ensure_product_index()
