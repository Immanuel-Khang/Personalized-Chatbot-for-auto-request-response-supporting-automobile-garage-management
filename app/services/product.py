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
    def search(self, query: str, limit: int = 3, fallback: bool = True) -> list[Product]:
        """Tìm sản phẩm theo từ trong tên (SQL ILIKE), xếp theo số từ khớp rồi id (thứ tự ổn định).
        fallback=False: không khớp tên thì trả rỗng để sales tự quyết định, KHÔNG trả xe đầu catalog."""
        words = _name_words(query)
        with SessionLocal() as db:
            items = []
            if words:
                conditions = [CatalogItem.name.ilike(f"%{w}%") for w in words]
                items = db.query(CatalogItem).filter(or_(*conditions)).order_by(CatalogItem.id).limit(50).all()
                items = sorted(items, key=lambda i: -sum(w in i.name.lower() for w in words))[:limit]
            if not items and fallback:
                # Không khớp tên cụ thể → trả vài xe đầu catalog
                items = (db.query(CatalogItem).filter(CatalogItem.kind.in_(VEHICLE_KINDS))
                         .order_by(CatalogItem.id).limit(limit).all())
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

    def search_by_budget(self, budget_vnd: float, limit: int = 3, offset: int = 0) -> list[Product]:
        """Xe có giá niêm yết ≤ ngân sách, gần ngân sách nhất trước (giá giảm dần, rồi id: thứ tự ổn định để phân trang).
        Không có xe nào trong ngân sách → rỗng để sales nói thật, KHÔNG thay bằng xe vượt ngân sách."""
        with SessionLocal() as db:
            items = (
                db.query(CatalogItem)
                .filter(CatalogItem.kind.in_(VEHICLE_KINDS), CatalogItem.list_price_vnd <= budget_vnd)
                .order_by(CatalogItem.list_price_vnd.desc(), CatalogItem.id)
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [_to_product(i) for i in items]

    def count_by_budget(self, budget_vnd: float) -> int:
        """Số mẫu xe có giá niêm yết ≤ ngân sách (để biết còn mẫu nào chưa gửi khách)."""
        with SessionLocal() as db:
            return (db.query(func.count(CatalogItem.id))
                    .filter(CatalogItem.kind.in_(VEHICLE_KINDS), CatalogItem.list_price_vnd <= budget_vnd).scalar())

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
