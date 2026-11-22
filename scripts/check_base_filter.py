#!/usr/bin/env python3
"""Check if RAG results pass _base() filter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text as sql_text
from app.db.session import engine, SessionLocal
from app.db.models import CatalogProduct, ProductPrice
from app.services.product import _base
from app.rag.factory import build_embedder
from app.rag.pgstore import _lit


def main():
    embedder = build_embedder()
    query = "xe rẻ, bền và tiết kiệm nhiên liệu"

    # Get RAG results
    query_vec = embedder.embed_query(query)
    q = _lit(query_vec)

    sql = """SELECT source, 1 - (embedding <=> CAST(:q AS vector)) AS score
             FROM rag_chunks
             WHERE namespace='products' AND kind='product' AND embedding IS NOT NULL
             ORDER BY embedding <=> CAST(:q AS vector)
             LIMIT 9"""

    with engine.connect() as conn:
        rows = conn.execute(sql_text(sql), {"q": q}).all()

    print("="*60)
    print("CHECKING IF RAG RESULTS PASS _base() FILTER")
    print("="*60)

    # Extract product IDs
    product_ids = []
    for source, score in rows:
        if source.startswith("product_") and ".catalog" in source:
            try:
                pid = int(source.split("_")[1].split(".")[0])
                product_ids.append(pid)
            except (ValueError, IndexError):
                pass

    print(f"\n1. RAG returned {len(product_ids)} product IDs: {product_ids[:5]}...")

    # Check which pass _base() filter
    with SessionLocal() as db:
        filtered_rows = _base(db).filter(CatalogProduct.id.in_(product_ids)).all()

        print(f"\n2. {len(filtered_rows)} products pass _base() filter")

        if not filtered_rows:
            print("\n❌ NO PRODUCTS PASS THE FILTER!")
            print("\n3. Debugging why products fail _base() filter:")

            for pid in product_ids[:5]:
                p = db.query(CatalogProduct).filter(CatalogProduct.id == pid).first()
                if not p:
                    print(f"\n   Product {pid}: NOT FOUND in catalog")
                    continue

                print(f"\n   Product {pid}: {p.name}")
                print(f"   - status: {p.status} (need: ACTIVE)")
                print(f"   - kind: {p.kind}")

                # Check price conditions
                price = db.query(ProductPrice).filter(
                    ProductPrice.product_id == pid,
                    ProductPrice.price_type == "MSRP",
                    ProductPrice.is_current == True
                ).first()

                if not price:
                    print(f"   - ❌ No current MSRP price")
                    # Check what prices exist
                    all_prices = db.query(ProductPrice).filter(
                        ProductPrice.product_id == pid
                    ).all()
                    if all_prices:
                        print(f"   - Found {len(all_prices)} prices:")
                        for pr in all_prices:
                            print(f"     * type={pr.price_type}, is_current={pr.is_current}, "
                                  f"effective_date={pr.effective_date}, needs_review={pr.needs_review}")
                else:
                    print(f"   - ✓ Has current MSRP: {price.amount_vnd:,} VND")
                    print(f"     effective_date: {price.effective_date}")
                    print(f"     needs_review: {price.needs_review}")
        else:
            print("\n✓ Products that pass filter:")
            for row in filtered_rows[:5]:
                p = row[0]
                price = row[1]
                print(f"   - {p.id}: {p.name} ({price.amount_vnd:,} VND)")


if __name__ == "__main__":
    sys.exit(main())
