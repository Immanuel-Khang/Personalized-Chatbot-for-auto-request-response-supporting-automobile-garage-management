"""Seed từ CSV và lệnh bật ACTIVE / retire dữ liệu mẫu (app/db/catalog_activate.py)."""
import pytest

from app.db.catalog_activate import activate, ready_drafts, sample_products
from app.db.models import CatalogProduct, ProductPrice
from app.db.seed import seed_catalog
from app.db.session import SessionLocal


@pytest.fixture(autouse=True)
def _seeded():
    seed_catalog()


def test_seed_activates_only_products_with_a_trusted_price():
    with SessionLocal() as db:
        by_status = {s: db.query(CatalogProduct).filter_by(status=s).count() for s in ("ACTIVE", "DRAFT")}
        assert by_status["ACTIVE"] + by_status["DRAFT"] == 52
        assert ready_drafts(db) == []  # seed đã bật mọi sản phẩm đủ điều kiện
        for p in db.query(CatalogProduct).filter_by(status="ACTIVE"):
            price = db.query(ProductPrice).filter_by(product_id=p.id, is_current=True).one()
            assert price.effective_date and not price.needs_review


def test_products_with_price_or_identity_problems_stay_draft():
    with SessionLocal() as db:
        drafts = {p.name for p in db.query(CatalogProduct).filter_by(status="DRAFT")}
    assert "DONGBEN (chưa rõ model, dòng 73)" in drafts  # thiếu model
    assert "ISUZU QLR77FE5" in drafts  # giá 561816 lệch thang, không có giá
    assert any(n.startswith("JAC") and "N350S" in n for n in drafts)  # hai mức giá, needs_review


def test_retire_samples_only_touches_unsourced_active_products():
    with SessionLocal() as db:
        sample = CatalogProduct(kind="TRUCK", name="ZZTEST Sample", status="ACTIVE")
        db.add(sample)
        db.commit()
        sample_id = sample.id
    try:
        with SessionLocal() as db:
            assert [p.id for p in sample_products(db)] == [sample_id]
            result = activate(db, retire_samples=True)
            db.commit()
            assert result["retired"] == [f"{sample_id} ZZTEST Sample"]
            assert db.get(CatalogProduct, sample_id).status == "RETIRED"
            assert db.query(CatalogProduct).filter_by(status="ACTIVE").count() > 40  # sản phẩm có nguồn giữ nguyên
    finally:
        with SessionLocal() as db:
            db.query(CatalogProduct).filter_by(id=sample_id).delete()
            db.commit()


def test_retire_is_refused_if_it_would_leave_no_active_product():
    with SessionLocal() as db:
        keep = db.query(CatalogProduct).filter_by(status="ACTIVE").all()
        for p in keep:
            p.status = "DRAFT"
        sample = CatalogProduct(kind="TRUCK", name="ZZTEST Only", status="ACTIVE")
        db.add(sample)
        db.flush()
        for p in keep:  # chỉ còn mẫu ACTIVE, không có gì thay thế
            p.status = "RETIRED"
        with pytest.raises(SystemExit):
            activate(db, retire_samples=True)
        db.rollback()
