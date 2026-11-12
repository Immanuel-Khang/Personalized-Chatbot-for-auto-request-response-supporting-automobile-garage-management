"""[TRACK A] ProductService: đọc catalog từ DB. SQL-side filtering – không load toàn bộ catalog vào bộ nhớ."""
import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.contracts.schemas import Product
from app.db.models import CatalogProduct, ProductPrice, VehicleSpec
from app.db.session import SessionLocal

# Loại sản phẩm là xe (catalog hiện chỉ có TRUCK, có thể thêm CAR); PART là phụ tùng
VEHICLE_KINDS = ("CAR", "TRUCK")
# Từ có trong tên nhiều mẫu nhưng không chỉ một dòng xe cụ thể ('xe tải' không phải tên mẫu)
GENERIC_WORDS = {"xe", "tải", "thùng", "chassis", "series", "new", "plus", "pro", "trước"}

_PRICE = ProductPrice.amount_vnd


def _name_words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and not w.isdigit() and w not in GENERIC_WORDS]  # "800 triệu" không khớp "N800"


def _base(db: Session, kinds: tuple[str, ...] = VEHICLE_KINDS):
    """Sản phẩm bot được phép nói tới: ACTIVE, có giá MSRP hiện hành đã duyệt và có ngày hiệu lực
    (giá luôn phải kèm 'giá áp dụng từ ngày ...')."""
    return (db.query(CatalogProduct, ProductPrice, VehicleSpec)
            .join(ProductPrice, (ProductPrice.product_id == CatalogProduct.id)
                  & (ProductPrice.price_type == "MSRP") & ProductPrice.is_current)
            .outerjoin(VehicleSpec, VehicleSpec.product_id == CatalogProduct.id)
            .filter(CatalogProduct.kind.in_(kinds), CatalogProduct.status == "ACTIVE",
                    ProductPrice.effective_date.isnot(None), ProductPrice.needs_review.is_(False)))


def _matches(word: str):
    """Từ khớp tên hoặc bí danh (aliases)."""
    pattern = f"%{word}%"
    return or_(CatalogProduct.name.ilike(pattern), func.array_to_string(CatalogProduct.aliases, " ").ilike(pattern))


def _haystack(p: CatalogProduct) -> str:
    return " ".join([p.name, *p.aliases]).lower()


def _specs(s: VehicleSpec | None) -> dict:
    """Giữ đúng các khóa agent đang dùng (seats/engine/transmission/fuel); mục chưa biết thì bỏ, không điền 0/rỗng."""
    if s is None:
        return {}
    found = {"seats": s.seats, "engine": s.engine_model, "transmission": s.gearbox_model, "fuel": s.fuel_type}
    return {k: v for k, v in found.items() if v is not None}


def _to_product(row: tuple[CatalogProduct, ProductPrice, VehicleSpec | None]) -> Product:
    p, price, spec = row
    return Product(id=p.id, kind=p.kind, name=p.name, list_price_vnd=price.amount_vnd,
                   price_as_of=price.effective_date.isoformat(), specs=_specs(spec))


class DbProductService:
    def search(self, query: str = "", limit: int = 3, fallback: bool = True,
               max_budget_vnd: float | None = None) -> list[Product]:
        """Tìm xe theo từ trong tên (SQL ILIKE) và/hoặc ngân sách tối đa.
        Có từ khớp tên: xếp theo số từ khớp rồi id. Chỉ có ngân sách: giá gần ngân sách nhất trước.
        Ngân sách là giới hạn cứng: KHÔNG bao giờ trả xe vượt ngân sách, kể cả khi fallback.
        fallback=False: không khớp thì trả rỗng để sales tự quyết định, KHÔNG trả xe đầu catalog."""
        words = _name_words(query or "")
        with SessionLocal() as db:
            base = _base(db)
            if max_budget_vnd is not None:
                base = base.filter(_PRICE <= max_budget_vnd)
            rows = []
            if words:
                rows = base.filter(or_(*[_matches(w) for w in words])).order_by(CatalogProduct.id).limit(50).all()
                rows = sorted(rows, key=lambda r: -sum(w in _haystack(r[0]) for w in words))[:limit]
            if not rows and (fallback or not words):
                order = ((_PRICE.desc(), CatalogProduct.id) if max_budget_vnd is not None else (CatalogProduct.id,))
                rows = base.order_by(*order).limit(limit).all()
            return [_to_product(r) for r in rows]

    def find_model(self, name: str, limit: int = 3) -> list[Product]:
        """Tra đúng dòng xe khách nêu: tên xe phải chứa MỌI từ của tên khách nói ('Toyota Vios' không khớp
        'Má phanh Toyota'). Không có thì trả rỗng, không thay bằng xe khác.
        Số và từ ngắn cũng tính ('Aumark-7.5T', '9T', 'WB 3800' phân biệt các mẫu cùng dòng)."""
        words = [w for w in re.findall(r"\w+(?:\.\w+)*", name.lower()) if w not in GENERIC_WORDS]
        if not words:
            return []
        with SessionLocal() as db:
            rows = _base(db).filter(*[_matches(w) for w in words]).order_by(CatalogProduct.id).limit(limit).all()
            return [_to_product(r) for r in rows]

    def list_vehicles(self, limit: int = 10) -> list[Product]:
        """Khách chủ động xem danh mục: xe theo thứ tự cố định (id), không phải gợi ý theo nhu cầu."""
        with SessionLocal() as db:
            return [_to_product(r) for r in _base(db).order_by(CatalogProduct.id).limit(limit).all()]

    def vehicle_summary(self) -> dict[str, int]:
        """Số mẫu xe theo loại (VD {'TRUCK': 55}) để bot nói đúng phạm vi catalog."""
        with SessionLocal() as db:
            sub = _base(db).with_entities(CatalogProduct.id, CatalogProduct.kind).subquery()
            rows = db.query(sub.c.kind, func.count(sub.c.id)).group_by(sub.c.kind).all()
            return {kind: count for kind, count in rows}
