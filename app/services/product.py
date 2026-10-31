"""[TRACK A] ProductService: đọc catalog từ DB. SQL-side filtering – không load toàn bộ catalog vào bộ nhớ."""
import re
from typing import Optional

from sqlalchemy import func, or_

from app.contracts.schemas import Product
from app.db.models import CatalogItem
from app.db.session import SessionLocal

# Loại sản phẩm là xe (catalog hiện chỉ có TRUCK, có thể thêm CAR); PART là phụ tùng
VEHICLE_KINDS = ("CAR", "TRUCK")
# Từ có trong tên nhiều mẫu nhưng không chỉ một dòng xe cụ thể ('xe tải' không phải tên mẫu)
GENERIC_WORDS = {"xe", "tải", "thùng", "chassis", "series", "new", "plus", "pro", "trước"}


def _name_words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and not w.isdigit() and w not in GENERIC_WORDS]  # "800 triệu" không khớp "N800"


def _to_product(i: CatalogItem) -> Product:
    return Product(id=i.id, kind=i.kind, name=i.name, list_price_vnd=i.list_price_vnd,
                   price_as_of=i.price_as_of, specs=i.specs)


class DbProductService:
    def search(self, query: str = "", limit: int = 3, fallback: bool = True,
               max_budget_vnd: float | None = None) -> list[Product]:
        """Tìm xe theo từ trong tên (SQL ILIKE) và/hoặc ngân sách tối đa.
        Có từ khớp tên: xếp theo số từ khớp rồi id. Chỉ có ngân sách: giá gần ngân sách nhất trước.
        Ngân sách là giới hạn cứng: KHÔNG bao giờ trả xe vượt ngân sách, kể cả khi fallback.
        fallback=False: không khớp thì trả rỗng để sales tự quyết định, KHÔNG trả xe đầu catalog."""
        words = _name_words(query or "")
        with SessionLocal() as db:
            base = db.query(CatalogItem).filter(CatalogItem.kind.in_(VEHICLE_KINDS))
            if max_budget_vnd is not None:
                base = base.filter(CatalogItem.list_price_vnd <= max_budget_vnd)
            items = []
            if words:
                items = base.filter(or_(*[CatalogItem.name.ilike(f"%{w}%") for w in words])
                                    ).order_by(CatalogItem.id).limit(50).all()
                items = sorted(items, key=lambda i: -sum(w in i.name.lower() for w in words))[:limit]
            if not items and (fallback or not words):
                order = ((CatalogItem.list_price_vnd.desc(), CatalogItem.id) if max_budget_vnd is not None
                         else (CatalogItem.id,))
                items = base.order_by(*order).limit(limit).all()
            return [_to_product(i) for i in items]

    def find_model(self, name: str, limit: int = 3) -> list[Product]:
        """Tra đúng dòng xe khách nêu: tên xe phải chứa MỌI từ của tên khách nói ('Toyota Vios' không khớp
        'Má phanh Toyota'). Không có thì trả rỗng, không thay bằng xe khác."""
        words = _name_words(name)
        if not words:
            return []
        with SessionLocal() as db:
            items = (db.query(CatalogItem)
                     .filter(CatalogItem.kind.in_(VEHICLE_KINDS), *[CatalogItem.name.ilike(f"%{w}%") for w in words])
                     .order_by(CatalogItem.id).limit(limit).all())
            return [_to_product(i) for i in items]

    def list_vehicles(self, limit: int = 10) -> list[Product]:
        """Khách chủ động xem danh mục: xe theo thứ tự cố định (id), không phải gợi ý theo nhu cầu."""
        with SessionLocal() as db:
            items = (db.query(CatalogItem).filter(CatalogItem.kind.in_(VEHICLE_KINDS))
                     .order_by(CatalogItem.id).limit(limit).all())
            return [_to_product(i) for i in items]

    def vehicle_summary(self) -> dict[str, int]:
        """Số mẫu xe theo loại (VD {'TRUCK': 55}) để bot nói đúng phạm vi catalog."""
        with SessionLocal() as db:
            rows = (db.query(CatalogItem.kind, func.count(CatalogItem.id))
                    .filter(CatalogItem.kind.in_(VEHICLE_KINDS)).group_by(CatalogItem.kind).all())
            return {kind: count for kind, count in rows}

    def get_popular(self, limit: int = 3) -> list[Product]:
        """Trả danh sách xe phổ biến (hiện tại theo thứ tự catalog, sau có thể dùng lượt xem thực)."""
        with SessionLocal() as db:
            items = (db.query(CatalogItem).filter(CatalogItem.kind.in_(VEHICLE_KINDS))
                     .order_by(CatalogItem.id).limit(limit).all())
            return [_to_product(i) for i in items]

    def get_by_id(self, product_id: int) -> Optional[Product]:
        """Tra đúng một mẫu xe theo id (định danh ổn định) sau khi đã xác định khách nhắc mẫu nào."""
        with SessionLocal() as db:
            item = db.get(CatalogItem, product_id)
            return _to_product(item) if item and item.kind in VEHICLE_KINDS else None

    def get_by_name(self, name: str) -> Optional[Product]:
        """Tìm xe theo tên (case-insensitive partial match)."""
        with SessionLocal() as db:
            item = db.query(CatalogItem).filter(CatalogItem.name.ilike(f"%{name}%")).first()
            return _to_product(item) if item else None
