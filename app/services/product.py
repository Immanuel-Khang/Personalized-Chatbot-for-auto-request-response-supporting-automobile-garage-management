"""[TRACK A] ProductService: đọc catalog từ DB. Thêm get_by_name cho sales agent."""
import re
from typing import Optional

from app.contracts.schemas import Product
from app.db.models import CatalogItem
from app.db.session import SessionLocal


class DbProductService:
    def search(self, query: str, limit: int = 3) -> list[Product]:
        q = set(re.findall(r"\w+", query.lower()))
        with SessionLocal() as db:
            items = db.query(CatalogItem).all()
        scored = sorted(
            items, key=lambda i: len(q & set(re.findall(r"\w+", i.name.lower()))), reverse=True
        )
        # Không khớp tên nào -> trả vài xe đầu bảng để khách có cái nhìn
        if scored and len(q & set(re.findall(r"\w+", scored[0].name.lower()))) == 0:
            scored = [i for i in items if i.kind == "CAR"]
        return [
            Product(id=i.id, kind=i.kind, name=i.name, list_price_vnd=i.list_price_vnd,
                    price_as_of=i.price_as_of, specs=i.specs)
            for i in scored[:limit]
        ]

    def get_by_name(self, name: str) -> Optional[Product]:
        """Tìm xe theo tên (case-insensitive partial match). Port từ codebase cũ."""
        with SessionLocal() as db:
            item = db.query(CatalogItem).filter(
                CatalogItem.name.ilike(f"%{name}%")
            ).first()
            if not item:
                return None
            return Product(id=item.id, kind=item.kind, name=item.name,
                           list_price_vnd=item.list_price_vnd,
                           price_as_of=item.price_as_of, specs=item.specs)
