#!/usr/bin/env python3
"""Debug why RAG search returns 0 products."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db.session import engine, SessionLocal
from app.db.models import CatalogProduct
from app.rag.factory import build_embedder
from app.rag.pgstore import _lit
from app.services.product import _base


def main():
    embedder = build_embedder()
    query = "xe rẻ, bền và tiết kiệm nhiên liệu"

    print("="*60)
    print("DEBUGGING RAG SEARCH RETURNING 0 PRODUCTS")
    print("="*60)

    # Step 1: Get RAG results
    query_vec = embedder.embed_query(query)
    q = _lit(query_vec)
    sql = """SELECT source, text, 1 - (embedding <=> CAST(:q AS vector)) AS score
             FROM rag_chunks
             WHERE namespace='knowledge' AND kind='product' AND embedding IS NOT NULL
             ORDER BY embedding <=> CAST(:q AS vector)
             LIMIT 9"""

    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"q": q, "n": 9}).all()

    print(f"\n1. RAG returned {len(rows)} chunks:")
    product_ids = []
    for i, (source, chunk_text, score) in enumerate(rows, 1):
        print(f"   {i}. {source} (score: {score:.3f})")
        if source.startswith("product_") and ".catalog" in source:
            try:
                pid = int(source.split("_")[1].split(".")[0])
                product_ids.append(pid)
                print(f"      → Product ID: {pid}")
            except (ValueError, IndexError):
                print(f"      → Failed to parse product ID")

    if not product_ids:
        print("\n❌ No product IDs extracted from RAG results!")
        return

    # Step 2: Check which products exist in catalog
    print(f"\n2. Checking {len(product_ids)} product IDs in catalog:")
    with SessionLocal() as db:
        existing = db.query(CatalogProduct.id, CatalogProduct.name, CatalogProduct.status).filter(
            CatalogProduct.id.in_(product_ids)
        ).all()

        existing_ids = {p.id for p in existing}
        print(f"   Found {len(existing)} products in catalog")
        for p in existing:
            print(f"   - ID {p.id}: {p.name} (status: {p.status})")

        missing_ids = set(product_ids) - existing_ids
        if missing_ids:
            print(f"\n   ⚠️  Missing IDs: {missing_ids}")

    # Step 3: Check which pass _base() filter
    print(f"\n3. Checking which products pass _base() filter:")
    with SessionLocal() as db:
        filtered = _base(db).filter(CatalogProduct.id.in_(product_ids)).all()
        print(f"   {len(filtered)} products pass _base() filter")

        if filtered:
            for row in filtered:
                p = row[0]  # CatalogProduct
                price = row[1]  # ProductPrice
                print(f"   ✓ ID {p.id}: {p.name}")
                print(f"     Price: {price.amount_vnd:,} VND (effective: {price.effective_date})")
        else:
            print("\n   ❌ NO PRODUCTS PASS THE FILTER!")

            # Debug why
            print("\n4. Debugging why products don't pass _base() filter:")
            with SessionLocal() as db:
                for pid in product_ids[:5]:  # Check first 5
                    p = db.query(CatalogProduct).filter(CatalogProduct.id == pid).first()
                    if not p:
                        continue

                    print(f"\n   Product ID {pid}: {p.name}")
                    print(f"   - status: {p.status} (need: ACTIVE)")
                    print(f"   - kind: {p.kind}")

                    # Check price
                    from app.db.models import ProductPrice
                    prices = db.query(ProductPrice).filter(
                        ProductPrice.product_id == pid,
                        ProductPrice.price_type == "MSRP"
                    ).all()

                    if not prices:
                        print(f"   - ❌ No MSRP price found")
                    else:
                        for price in prices:
                            print(f"   - Price: {price.amount_vnd:,} VND")
                            print(f"     is_current: {price.is_current}")
                            print(f"     effective_date: {price.effective_date}")
                            print(f"     needs_review: {price.needs_review}")


if __name__ == "__main__":
    sys.exit(main())
