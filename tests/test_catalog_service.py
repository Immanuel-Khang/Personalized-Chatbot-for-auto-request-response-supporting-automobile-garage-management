"""DbProductService trên schema danh mục mới (products + product_prices + vehicle_specs)."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import CatalogProduct, ProductPrice, VehicleSpec
from app.db.seed import seed_catalog
from app.db.session import SessionLocal
from app.services.product import DbProductService

svc = DbProductService()


@pytest.fixture(autouse=True)
def _seeded():
    seed_catalog()  # idempotent; test khác có thể đã xóa bảng


def _add(name, *, status="ACTIVE", amount=100_000_000, effective=date(2026, 10, 18), needs_review=False,
         aliases=(), kind="TRUCK") -> int:
    with SessionLocal() as db:
        p = CatalogProduct(kind=kind, name=name, status=status, aliases=list(aliases))
        p.spec = VehicleSpec(seats=3)
        p.prices = [ProductPrice(amount_vnd=amount, effective_date=effective, needs_review=needs_review)]
        db.add(p)
        db.commit()
        return p.id


def _delete(*ids: int) -> None:
    with SessionLocal() as db:
        db.query(ProductPrice).filter(ProductPrice.product_id.in_(ids)).delete()
        db.query(VehicleSpec).filter(VehicleSpec.product_id.in_(ids)).delete()
        db.query(CatalogProduct).filter(CatalogProduct.id.in_(ids)).delete()
        db.commit()


def test_find_model_returns_contract_fields():
    [p] = svc.find_model("FUSO FI170L")
    assert p.name == "FUSO FI170L"
    assert p.list_price_vnd == 845_000_000
    assert p.price_as_of == "2026-10-18"
    assert p.specs["price_basis"] == "giá xe chassis, chưa gồm thùng"  # giá nguồn là giá khung gầm
    assert p.specs["seats"] and p.specs["engine"]


def test_budget_is_a_hard_limit_even_with_fallback():
    found = svc.search("FOTON", limit=5, fallback=True, max_budget_vnd=400_000_000)
    assert found and all(p.list_price_vnd <= 400_000_000 for p in found)
    assert svc.search("", limit=5, max_budget_vnd=1) == []


@pytest.mark.parametrize("kwargs", [
    {"status": "DRAFT"}, {"status": "RETIRED"}, {"needs_review": True}, {"effective": None}])
def test_unusable_products_are_never_offered(kwargs):
    pid = _add("ZZTEST Hidden", **kwargs)
    try:
        assert svc.find_model("ZZTEST Hidden") == []
        assert all(p.id != pid for p in svc.list_vehicles(limit=500))
    finally:
        _delete(pid)


def test_aliases_are_searchable():
    pid = _add("ZZTEST Official-4.5T", aliases=["ZZTEST Miler"])
    try:
        assert [p.id for p in svc.find_model("ZZTEST Miler")] == [pid]
    finally:
        _delete(pid)


def test_summary_counts_vehicles_only():
    summary = svc.vehicle_summary()
    assert set(summary) == {"TRUCK"}
    assert 40 <= summary["TRUCK"] <= 52  # 52 sản phẩm nạp từ CSV, một số chưa đủ điều kiện ACTIVE


def test_price_above_int32_is_stored():
    pid = _add("ZZTEST Heavy", amount=3_500_000_000)
    try:
        assert svc.find_model("ZZTEST Heavy")[0].list_price_vnd == 3_500_000_000
    finally:
        _delete(pid)


def test_only_one_current_price_per_product_and_type():
    pid = _add("ZZTEST Dup")
    try:
        with SessionLocal() as db:
            db.add(ProductPrice(product_id=pid, amount_vnd=1, effective_date=date(2026, 10, 18)))
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        _delete(pid)
