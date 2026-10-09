"""[TRACK A] ProductService: đọc catalog từ DB. SQL-side filtering – không load toàn bộ catalog vào bộ nhớ."""
import re
from typing import Optional

from sqlalchemy import or_

from app.contracts.schemas import Product
from app.db.models import CatalogItem
from app.db.session import SessionLocal


def _to_product(i: CatalogItem) -> Product:
    return Product(id=i.id, kind=i.kind, name=i.name, list_price_vnd=i.list_price_vnd,
                   price_as_of=i.price_as_of, specs=i.specs)


class DbProductService:
    def search(self, query: str, limit: int = 3) -> list[Product]:
        """Tìm sản phẩm bằng SQL ILIKE – không load toàn catalog vào bộ nhớ."""
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        with SessionLocal() as db:
            if words:
                conditions = [CatalogItem.name.ilike(f"%{w}%") for w in words]
                items = db.query(CatalogItem).filter(or_(*conditions)).limit(limit).all()
            else:
                items = []
            if not items:
                # Không khớp tên cụ thể → trả vài xe đầu catalog
                items = db.query(CatalogItem).filter(CatalogItem.kind == "CAR").limit(limit).all()
            return [_to_product(i) for i in items]

    def search_by_budget(self, budget_vnd: float, limit: int = 3) -> list[Product]:
        """Trả xe phù hợp ngân sách (≤ budget * 1.1), sắp xếp giá giảm dần."""
        with SessionLocal() as db:
            items = (
                db.query(CatalogItem)
                .filter(CatalogItem.kind == "CAR", CatalogItem.list_price_vnd <= budget_vnd * 1.1)
                .order_by(CatalogItem.list_price_vnd.desc())
                .limit(limit)
                .all()
            )
            if not items:
                # Ngân sách quá thấp → trả xe rẻ nhất trong catalog
                items = (
                    db.query(CatalogItem)
                    .filter(CatalogItem.kind == "CAR")
                    .order_by(CatalogItem.list_price_vnd.asc())
                    .limit(limit)
                    .all()
                )
            return [_to_product(i) for i in items]

    def get_popular(self, limit: int = 3) -> list[Product]:
        """Trả danh sách xe phổ biến (hiện tại theo thứ tự catalog, sau có thể dùng lượt xem thực)."""
        with SessionLocal() as db:
            items = db.query(CatalogItem).filter(CatalogItem.kind == "CAR").limit(limit).all()
            return [_to_product(i) for i in items]

    def get_by_name(self, name: str) -> Optional[Product]:
        """Tìm xe theo tên (case-insensitive partial match)."""
        with SessionLocal() as db:
            item = db.query(CatalogItem).filter(CatalogItem.name.ilike(f"%{name}%")).first()
            return _to_product(item) if item else None
