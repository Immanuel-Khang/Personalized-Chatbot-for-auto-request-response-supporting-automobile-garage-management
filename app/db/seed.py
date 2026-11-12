"""Nạp dữ liệu mẫu từ data/catalog.json lần đầu chạy. Người A thay bằng dữ liệu thật của xưởng."""
import json
from datetime import date
from pathlib import Path

from app.db.models import CatalogProduct, ProductPrice, VehicleSpec
from app.db.session import SessionLocal

DATA = Path(__file__).resolve().parents[2] / "data"


def seed_catalog() -> None:
    with SessionLocal() as db:
        if db.query(CatalogProduct).count() > 0:
            return
        for row in json.loads((DATA / "catalog.json").read_text(encoding="utf-8")):
            product = CatalogProduct(kind=row["kind"], name=row["name"], status="ACTIVE", video_url=row.get("video_url"))
            specs = row.get("specs") or {}
            if row["kind"] != "PART":
                product.spec = VehicleSpec(
                    seats=specs.get("seats"), engine_model=specs.get("engine"),
                    gearbox_model=specs.get("transmission"), fuel_type=specs.get("fuel"), spec_raw=specs)
            product.prices = [ProductPrice(
                price_type="MSRP", amount_vnd=row["list_price_vnd"],
                effective_date=date.fromisoformat(row["price_as_of"]))]
            db.add(product)
        db.commit()
