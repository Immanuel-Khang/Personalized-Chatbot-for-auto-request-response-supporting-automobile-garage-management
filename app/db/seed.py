"""Nạp dữ liệu mẫu từ data/catalog.json lần đầu chạy. Người A thay bằng dữ liệu thật của xưởng."""
import json
from pathlib import Path

from app.db.models import CatalogItem
from app.db.session import SessionLocal

DATA = Path(__file__).resolve().parents[2] / "data"


def seed_catalog() -> None:
    with SessionLocal() as db:
        if db.query(CatalogItem).count() > 0:
            return
        for row in json.loads((DATA / "catalog.json").read_text(encoding="utf-8")):
            db.add(CatalogItem(
                kind=row["kind"], name=row["name"], list_price_vnd=row["list_price_vnd"],
                price_as_of=row["price_as_of"],
                specs_json=json.dumps(row.get("specs", {}), ensure_ascii=False),
                video_url=row.get("video_url"),
            ))
        db.commit()
