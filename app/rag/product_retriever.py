"""
Product RAG retriever: tìm kiếm sản phẩm theo ngôn ngữ tự nhiên.

Tương tự HybridRetriever nhưng cho catalog products. Index gồm:
- Tên sản phẩm, brand, model
- Thông số kỹ thuật quan trọng (tải trọng, wheelbase, động cơ)
- Sales info (selling points, use cases, benefits)
- Mô tả (suitable_use, route_type, cargo_weight...)

Mỗi product = 1 chunk. Namespace = "products" trong rag_chunks.
"""
import json
import logging
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.contracts.schemas import Product
from app.db.models import CatalogProduct, ProductPrice, VehicleSpec
from app.db.session import SessionLocal
from app.rag.chunking import Chunk
from app.rag.embeddings import Embedder
from app.rag.text import normalize

logger = logging.getLogger(__name__)

NAMESPACE = "products"


class ProductSearchResult(Protocol):
    """Kết quả tìm kiếm product."""
    product_id: int
    score: float


def _build_index_text(product: CatalogProduct, spec: VehicleSpec | None) -> str:
    """Tạo text để index: gộp tên, specs, sales_info thành một đoạn văn."""
    parts = [product.name]

    # Thêm aliases
    if product.aliases:
        parts.extend(product.aliases)

    # Thông số kỹ thuật quan trọng
    if spec:
        if spec.vehicle_type:
            parts.append(f"loại xe {spec.vehicle_type}")
        if spec.payload_kg:
            parts.append(f"tải trọng {spec.payload_kg} kg")
        if spec.gvm_kg:
            parts.append(f"tổng tải {spec.gvm_kg} kg")
        if spec.wheelbase_mm:
            parts.append(f"chiều dài cơ sở {spec.wheelbase_mm} mm")
        if spec.engine_model:
            parts.append(f"động cơ {spec.engine_model}")
        if spec.power_kw:
            parts.append(f"công suất {spec.power_kw} kW")
        if spec.emission_standard:
            parts.append(f"tiêu chuẩn khí thải {spec.emission_standard}")

    # Sales info
    if product.sales_info:
        info = product.sales_info

        # Selling points
        if "selling_points" in info:
            parts.extend(info["selling_points"])

        # Use cases and benefits
        for key in ("customer_benefit", "competitive_advantage", "primary_use_case",
                    "suitable_for", "suitable_use", "route_type", "cargo_weight"):
            if key in info and info[key]:
                parts.append(str(info[key]))

        # FAQs
        if "faqs" in info:
            for faq in info["faqs"]:
                if "question" in faq:
                    parts.append(faq["question"])
                if "answer" in faq:
                    parts.append(faq["answer"])

    return " ".join(str(p) for p in parts if p)


def _build_chunk(product: CatalogProduct, spec: VehicleSpec | None, price: ProductPrice) -> Chunk:
    """Tạo Chunk từ product để lưu vào rag_chunks."""
    full_index_text = _build_index_text(product, spec)

    # Text hiển thị (ngắn gọn hơn)
    display_parts = [product.name]
    if spec and spec.payload_kg:
        display_parts.append(f"Tải trọng: {spec.payload_kg} kg")
    if price:
        display_parts.append(f"Giá: {price.amount_vnd:,} VNĐ")

    # Chunk is frozen dataclass, id is computed property
    # Use a unique source that includes product.id so the computed id will be unique
    # IMPORTANT: Store full_index_text in 'text' field because that's what gets indexed
    # The 'section' field is used by Chunk.index_text property to compute the final index text
    return Chunk(
        text=full_index_text,  # Full searchable text with specs and sales_info
        source=f"product_{product.id}.catalog",
        section="",  # Empty so Chunk.index_text property returns text as-is
        kind="product",
        tags=tuple(),
    )


def load_product_chunks() -> list[Chunk]:
    """Load tất cả products ACTIVE có giá thành chunks để index."""
    with SessionLocal() as db:
        rows = (db.query(CatalogProduct, ProductPrice, VehicleSpec)
                .join(ProductPrice, (ProductPrice.product_id == CatalogProduct.id)
                      & (ProductPrice.price_type == "MSRP") & ProductPrice.is_current)
                .outerjoin(VehicleSpec, VehicleSpec.product_id == CatalogProduct.id)
                .filter(CatalogProduct.status == "ACTIVE",
                        ProductPrice.effective_date.isnot(None),
                        ProductPrice.needs_review.is_(False))
                .all())

    chunks = [_build_chunk(p, s, pr) for p, pr, s in rows]
    logger.info("Loaded %d product chunks for indexing", len(chunks))
    return chunks


class ProductRetriever:
    """RAG retriever cho products, dùng pgvector."""

    def __init__(self, engine: Engine, embedder: Embedder | None = None) -> None:
        from app.db.models import RAG_EMBEDDING_DIM
        self.engine = engine
        self.embedder = embedder
        self.dim = RAG_EMBEDDING_DIM
        self.chunks: list[Chunk] = []
        self._product_ids: dict[str, int] = {}  # chunk_id -> product_id
        self._load_mapping()  # Load existing mapping from DB

    def _load_mapping(self) -> None:
        """Load chunk_id -> product_id mapping from rag_chunks."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, source FROM rag_chunks WHERE namespace=:ns"),
                {"ns": NAMESPACE}
            ).all()
            for chunk_id, source in rows:
                # source format: "product_{id}.catalog"
                try:
                    product_id = int(source.split("_")[1].split(".")[0])
                    self._product_ids[chunk_id] = product_id
                except (IndexError, ValueError) as e:
                    logger.warning("Failed to parse product_id from source %s: %s", source, e)

    def sync(self) -> dict:
        """Đồng bộ products vào rag_chunks (namespace='products')."""
        chunks = load_product_chunks()
        self.chunks = chunks
        # Extract product_id from source (format: "product_{id}.catalog")
        self._product_ids = {c.id: int(c.source.split("_")[1].split(".")[0]) for c in chunks}

        if not chunks:
            logger.warning("No products to index")
            return {"chunks": 0, "embedded": 0, "deleted": 0}

        ids = [c.id for c in chunks]
        model = getattr(self.embedder, "name", None) if self.embedder else None

        # Kiểm tra chunks nào cần embed
        with self.engine.connect() as conn:
            existing = dict(conn.execute(
                text("SELECT id, embedding_model FROM rag_chunks WHERE namespace=:ns"),
                {"ns": NAMESPACE}).all())
            has_vec = {r[0] for r in conn.execute(
                text("SELECT id FROM rag_chunks WHERE namespace=:ns AND embedding IS NOT NULL"),
                {"ns": NAMESPACE})}

        need = [c for c in chunks if self.embedder and not (c.id in has_vec and existing.get(c.id) == model)]
        vecs = []

        if need and self.embedder:
            try:
                vecs = self.embedder.embed_documents([c.index_text for c in need])
                for v in vecs:
                    if len(v) != self.dim:
                        raise ValueError(f"embedding {len(v)} chiều, cột rag_chunks.embedding là {self.dim} chiều")
            except Exception as e:
                logger.error("Failed to embed products: %s", e)
                return {"chunks": 0, "embedded": 0, "deleted": 0, "error": str(e)}

        # Upsert chunks
        rows = [{"ns": NAMESPACE, "id": c.id, "source": c.source, "section": c.section,
                 "kind": c.kind, "tags": list(c.tags), "text": c.text, "index_text": c.index_text,
                 "lex": normalize(c.index_text)}
                for c in chunks]

        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"rag_sync:{NAMESPACE}"})

            if rows:
                conn.execute(text("""
                    INSERT INTO rag_chunks (namespace, id, source, section, kind, tags, text, index_text, lex_text)
                    VALUES (:ns, :id, :source, :section, :kind, :tags, :text, :index_text, :lex)
                    ON CONFLICT (namespace, id) DO UPDATE SET source=EXCLUDED.source, section=EXCLUDED.section,
                      kind=EXCLUDED.kind, tags=EXCLUDED.tags, text=EXCLUDED.text, index_text=EXCLUDED.index_text,
                      lex_text=EXCLUDED.lex_text, updated_at=now()"""), rows)

            if need and vecs:
                def _lit(vec: list[float]) -> str:
                    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"

                conn.execute(text(
                    "UPDATE rag_chunks SET embedding=CAST(:v AS vector), embedding_model=:m, updated_at=now() "
                    "WHERE namespace=:ns AND id=:id"),
                    [{"v": _lit(v), "m": model, "ns": NAMESPACE, "id": c.id} for c, v in zip(need, vecs)])

            deleted = conn.execute(
                text("DELETE FROM rag_chunks WHERE namespace=:ns AND NOT (id = ANY(:ids))"),
                {"ns": NAMESPACE, "ids": ids}).rowcount

        logger.info("Product RAG sync: %d chunks, embedded %d, deleted %d", len(chunks), len(need), deleted)
        return {"chunks": len(chunks), "embedded": len(need), "deleted": deleted}

    def retrieve(self, query: str, k: int = 5, max_budget_vnd: float | None = None) -> list[int]:
        """Tìm products theo ngôn ngữ tự nhiên, trả product_ids xếp theo điểm RAG.

        Args:
            query: Câu hỏi/mô tả nhu cầu của khách
            k: Số sản phẩm tối đa trả về
            max_budget_vnd: Ngân sách tối đa (VNĐ), None = không giới hạn

        Returns:
            List product_ids xếp theo độ phù hợp
        """
        if not query.strip() or not self.embedder:
            return []

        try:
            vec = self.embedder.embed_query(query)
            if len(vec) != self.dim:
                logger.error("Query embedding dimension mismatch: %d vs %d", len(vec), self.dim)
                return []
        except Exception as e:
            logger.error("Failed to embed query: %s", e)
            return []

        def _lit(v: list[float]) -> str:
            return "[" + ",".join(f"{float(x):.7g}" for x in v) + "]"

        # Tìm theo vector similarity
        sql = """
            SELECT id, 1 - (embedding <=> CAST(:q AS vector)) as score
            FROM rag_chunks
            WHERE namespace=:ns AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
        """

        with self.engine.connect() as conn:
            hits = conn.execute(text(sql), {
                "q": _lit(vec),
                "ns": NAMESPACE,
                "k": k * 2  # Lấy nhiều hơn để lọc theo giá
            }).all()

        # Chuyển chunk_id -> product_id
        product_ids = [(self._product_ids[chunk_id], score) for chunk_id, score in hits if chunk_id in self._product_ids]

        # Lọc theo budget nếu có
        if max_budget_vnd is not None:
            with SessionLocal() as db:
                allowed = {r[0] for r in db.query(CatalogProduct.id)
                           .join(ProductPrice, (ProductPrice.product_id == CatalogProduct.id)
                                 & (ProductPrice.price_type == "MSRP") & ProductPrice.is_current)
                           .filter(ProductPrice.amount_vnd <= max_budget_vnd).all()}
                product_ids = [(pid, score) for pid, score in product_ids if pid in allowed]

        # Trả top k
        return [pid for pid, score in product_ids[:k]]
