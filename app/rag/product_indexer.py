"""Index product sales_info into RAG for natural language search."""
import hashlib
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import CatalogProduct
from app.rag.chunking import Chunk


@dataclass
class ProductChunk:
    """Product data formatted for RAG indexing."""
    product_id: int
    brand: str
    model: str
    name: str
    sales_summary: str

    def to_chunk(self) -> Chunk:
        """Convert to RAG Chunk format."""
        return Chunk(
            text=self.sales_summary,
            source=f"product_{self.product_id}.catalog",
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
            product_id=p.id,
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
