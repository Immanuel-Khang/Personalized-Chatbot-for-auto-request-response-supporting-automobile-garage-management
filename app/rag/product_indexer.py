"""Index product sales_info into RAG for natural language search."""
import hashlib
import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import CatalogProduct
from app.rag.chunking import Chunk

logger = logging.getLogger(__name__)
NAMESPACE = "products"


def source_for(sku: str) -> str:
    """Tên nguồn của chunk sản phẩm. Dùng sku (ổn định giữa các máy), KHÔNG dùng id tự tăng."""
    return f"product_{sku}.catalog"


def sku_from_source(source: str) -> str | None:
    if source.startswith("product_") and source.endswith(".catalog"):
        return source[len("product_"):-len(".catalog")] or None
    return None


@dataclass
class ProductChunk:
    """Product data formatted for RAG indexing."""
    sku: str
    brand: str
    model: str
    name: str
    sales_summary: str

    def to_chunk(self) -> Chunk:
        """Convert to RAG Chunk format."""
        return Chunk(
            text=self.sales_summary,
            source=source_for(self.sku),
            section=f"{self.brand} {self.model}",
            kind="product",
            tags=()
        )


def extract_product_chunks(db: Session) -> list[Chunk]:
    """Extract all products with sales_info into chunks for indexing."""
    from app.db.models import Brand, VehicleModel

    products = db.query(CatalogProduct).outerjoin(Brand).outerjoin(VehicleModel).filter(
        CatalogProduct.sales_info.isnot(None),
        CatalogProduct.status == "ACTIVE"
    ).all()

    chunks = []
    for p in products:
        if not p.sales_info or "summary" not in p.sales_info:
            continue

        # Câu "Không hợp: ..." không đưa vào chỉ mục: nếu không, 'xe chở hàng nặng' sẽ khớp xe nhẹ có ghi "Không hợp: hàng nặng".
        # Bản đầy đủ vẫn nằm trong products.sales_info cho agent đọc.
        summary = re.sub(r"\s*Không hợp:[^.]*\.", "", p.sales_info["summary"]).strip()
        if not summary:
            continue

        # Get brand and model via query
        brand = db.query(Brand).filter_by(id=p.brand_id).first() if p.brand_id else None
        model = db.query(VehicleModel).filter_by(id=p.model_id).first() if p.model_id else None

        pc = ProductChunk(
            sku=p.sku or f"id{p.id}",  # id{n} (chữ thường) chỉ cho dòng chưa có sku, vd dữ liệu mẫu
            brand=brand.name if brand else "",
            model=model.name if model else "",
            name=p.name,
            sales_summary=summary
        )
        chunks.append(pc.to_chunk())

    return chunks


def index_products(db: Session, embedder, store) -> dict:
    """Index all products into RAG store using existing infrastructure."""
    chunks = extract_product_chunks(db)
    result = store.sync(chunks, embedder)
    return result


def ensure_product_index() -> dict | None:
    """Đồng bộ chỉ mục sản phẩm lúc khởi động. Idempotent: chỉ embed chunk mới/đổi, xóa chunk thừa, có advisory lock.
    Không có embedder (thiếu OPENAI_API_KEY / EMBEDDING_PROVIDER=none) hoặc embed lỗi -> vẫn lưu chunk không vector
    để nhánh BM25 chạy. Không bao giờ làm app sập khi khởi động."""
    from app.db.session import SessionLocal, engine
    from app.rag.factory import build_embedder
    from app.rag.pgstore import PgVectorStore

    store = PgVectorStore(engine, namespace=NAMESPACE)
    try:
        embedder = build_embedder()
    except Exception:
        logger.warning("build_embedder lỗi, index sản phẩm không có vector", exc_info=True)
        embedder = None
    try:
        with SessionLocal() as db:
            try:
                result = index_products(db, embedder, store)
            except Exception:
                if embedder is None:
                    raise
                logger.warning("embed sản phẩm lỗi, lưu chunk không vector (chỉ BM25)", exc_info=True)
                result = index_products(db, None, store)
            status = check_product_index(db)
    except Exception:
        logger.error("Không đồng bộ được chỉ mục sản phẩm", exc_info=True)
        return None
    return {**result, **status}


def check_product_index(db: Session) -> dict:
    """So số chunk trong rag_chunks(products) với số sản phẩm ACTIVE có summary. Lệch -> log cảnh báo."""
    from sqlalchemy import text

    expected = len(extract_product_chunks(db))
    actual = db.execute(text("SELECT count(*) FROM rag_chunks WHERE namespace=:ns AND kind='product'"),
                        {"ns": NAMESPACE}).scalar_one()
    ok = expected == actual
    if not ok:
        logger.warning("Chỉ mục sản phẩm lệch: rag_chunks có %d chunk, cần %d (sản phẩm ACTIVE có summary). "
                       "Chạy: python scripts/index_products.py", actual, expected)
    return {"expected_chunks": expected, "indexed_chunks": actual, "in_sync": ok}
