"""[TRACK A] ProductService: đọc catalog từ DB. SQL-side filtering – không load toàn bộ catalog vào bộ nhớ."""
import logging
import re
from collections import Counter

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.contracts.schemas import Product
from app.db.models import CatalogProduct, ProductPrice, VehicleSpec
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

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
    """Trích xuất thông số kỹ thuật từ VehicleSpec; mục chưa biết thì bỏ, không điền 0/rỗng."""
    if s is None:
        return {}
    # Thông số cơ bản
    found = {
        "seats": s.seats,
        "vehicle_type": s.vehicle_type,
        "assembly": s.assembly,
        "wheel_formula": s.wheel_formula,
        # Khối lượng và tải trọng
        "curb_mass_kg": s.curb_mass_kg,
        "payload_kg": s.payload_kg,
        "gvm_kg": s.gvm_kg,
        # Kích thước tổng thể
        "wheelbase_mm": s.wheelbase_mm,
        "length_mm": s.length_mm,
        "width_mm": s.width_mm,
        "height_mm": s.height_mm,
        "dims_basis": s.dims_basis,
        "ground_clearance_mm": s.ground_clearance_mm,
        "turning_radius_m": float(s.turning_radius_m) if s.turning_radius_m else None,
        # Kích thước thùng
        "cargo_length_mm": s.cargo_length_mm,
        "cargo_width_mm": s.cargo_width_mm,
        "cargo_height_mm": s.cargo_height_mm,
        # Động cơ
        "engine": s.engine_model,
        "power_kw": float(s.power_kw) if s.power_kw else None,
        "power_rpm": s.power_rpm,
        "torque_nm": float(s.torque_nm) if s.torque_nm else None,
        "torque_rpm_min": s.torque_rpm_min,
        "torque_rpm_max": s.torque_rpm_max,
        "displacement_cc": s.displacement_cc,
        "emission_standard": s.emission_standard,
        "has_scr": s.has_scr,
        "fuel_type": s.fuel_type,
        "fuel_tank_l": s.fuel_tank_l,
        # Hộp số
        "transmission": s.gearbox_model,
        "gears_forward": s.gears_forward,
        "gears_reverse": s.gears_reverse,
        # Khác
        "tire_size": s.tire_size,
        "has_abs": s.has_abs,
    }
    # Chỉ giữ các trường có giá trị
    result = {k: v for k, v in found.items() if v is not None}
    # Thêm spec_extra nếu có
    if s.spec_extra:
        result["spec_extra"] = s.spec_extra
    return result


# Giá nguồn có thể là giá khung gầm hoặc kèm thùng; bot phải nói đúng phạm vi giá, không để khách hiểu là giá xe hoàn chỉnh.
PRICE_BASIS_TEXT = {"CHASSIS": "giá xe chassis, chưa gồm thùng", "FLATBED": "giá xe thùng lửng", "TARP": "giá xe thùng mui bạt"}


def _to_product(row: tuple[CatalogProduct, ProductPrice, VehicleSpec | None]) -> Product:
    p, price, spec = row
    specs = _specs(spec)
    if price.basis in PRICE_BASIS_TEXT:
        specs["price_basis"] = PRICE_BASIS_TEXT[price.basis]
    return Product(id=p.id, kind=p.kind, name=p.name, list_price_vnd=price.amount_vnd,
                   price_as_of=price.effective_date.isoformat(), specs=specs, sales_info=p.sales_info)


class DbProductService:
    def search_by_rag(self, query: str, limit: int = 3) -> list[Product]:
        """Tìm xe theo mô tả nhu cầu bằng ngôn ngữ tự nhiên (semantic search qua RAG).
        VD: 'xe rẻ và bền', 'tiết kiệm nhiên liệu', 'chở hàng nặng', 'phù hợp chạy đường dài'."""
        from sqlalchemy import text
        from app.config import settings
        from app.db.session import engine
        from app.rag.bm25 import BM25Index
        from app.rag.factory import build_embedder
        from app.rag.fusion import rrf
        from app.rag.pgstore import _lit
        from app.rag.text import content_words

        if not query.strip():
            return []
        with engine.connect() as conn:
            docs = conn.execute(text("SELECT source, index_text FROM rag_chunks WHERE namespace='products' AND kind='product'")).all()
        if not docs:
            return []

        # Nhánh từ khóa (BM25, cùng bộ chuẩn hóa tiếng Việt với knowledge RAG): chỉ giữ chunk khớp đủ RAG_MIN_KEYWORD_HITS từ
        sources = [d[0] for d in docs]
        index = BM25Index([d[1] for d in docs])
        # Từ có trong >60% sản phẩm ('xe', 'máy', 'tải'...) không phân biệt được xe nào nên không tính là khớp từ khóa
        common = {w for w, df in Counter(w for s in index.doc_sets for w in s).items() if df > 0.6 * len(docs)}
        q_words = content_words(query) - common
        need = min(settings.rag_min_keyword_hits, len(q_words))
        bm25 = [sources[i] for i, _s, _h in index.search(query, settings.rag_candidates)
                if need and len(q_words & index.doc_sets[i]) >= need]

        # Nhánh vector (pgvector): chỉ giữ chunk có cosine >= RAG_MIN_VECTOR_SIM. Lỗi/không có embedder -> chỉ dùng BM25
        vector: list[str] = []
        try:
            if embedder := build_embedder():
                # Chỉ so vector cùng model: hash và openai nằm ở hai không gian khác nhau, so lẫn là vô nghĩa
                sql = """SELECT source, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM rag_chunks
                         WHERE namespace='products' AND kind='product' AND embedding IS NOT NULL
                           AND embedding_model = :m
                         ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"""
                with engine.connect() as conn:
                    rows = conn.execute(text(sql), {"q": _lit(embedder.embed_query(query)), "n": settings.rag_candidates,
                                                    "m": embedder.name}).all()
                vector = [r[0] for r in rows if r[1] >= settings.rag_min_vector_sim]
        except Exception:
            logger.warning("product vector search failed, BM25-only", exc_info=True)

        fused = rrf([bm25, vector], [settings.rag_weight_bm25, settings.rag_weight_vector], settings.rag_rrf_k)

        # Chunk đặt tên product_<sku>.catalog; nối về products bằng sku (ổn định giữa các máy), không dùng id tự tăng
        from app.rag.product_indexer import sku_from_source
        ranked: list[str] = []
        for source, _score in sorted(fused.items(), key=lambda x: x[1], reverse=True):
            if (key := sku_from_source(source)) and key not in ranked:
                ranked.append(key)
        if not ranked:
            return []

        def _id_key(k: str) -> int | None:  # "id12": dòng chưa có sku (dữ liệu mẫu), xem product_indexer
            return int(k[2:]) if re.fullmatch(r"id\d+", k) else None

        sku_keys = [k for k in ranked if _id_key(k) is None]
        id_keys = [i for k in ranked if (i := _id_key(k)) is not None]
        with SessionLocal() as db:
            cond = [CatalogProduct.sku.in_(sku_keys)] if sku_keys else []
            if id_keys:
                cond.append(CatalogProduct.id.in_(id_keys))
            rows = _base(db).filter(or_(*cond)).all()
            by_key = {}
            for row in rows:
                by_key[row[0].sku or f"id{row[0].id}"] = _to_product(row)
            # Trả theo thứ tự liên quan của RAG, tối đa limit
            return [by_key[k] for k in ranked if k in by_key][:limit]

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
