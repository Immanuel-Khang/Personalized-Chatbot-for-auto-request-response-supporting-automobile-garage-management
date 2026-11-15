#!/usr/bin/env python3
"""Check current product IDs in catalog vs RAG."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text as sql_text
from app.db.session import engine, SessionLocal
from app.db.models import CatalogProduct
from app.services.product import _base


def main():
    print("="*60)
    print("PRODUCT IDs: CATALOG vs RAG")
    print("="*60)

    # Get product IDs that pass _base() filter
    with SessionLocal() as db:
        filtered = _base(db).all()
        catalog_ids = sorted([row[0].id for row in filtered])

    print(f"\n1. Products in catalog that pass _base() filter:")
    print(f"   Count: {len(catalog_ids)}")
    print(f"   ID range: {min(catalog_ids) if catalog_ids else 'N/A'} - {max(catalog_ids) if catalog_ids else 'N/A'}")
    print(f"   Sample IDs: {catalog_ids[:10]}")

    # Get product IDs in RAG
    with engine.connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT source FROM rag_chunks WHERE namespace='products' AND kind='product' ORDER BY source"
        )).all()

        rag_ids = []
        for (source,) in rows:
            if source.startswith("product_") and ".catalog" in source:
                try:
                    pid = int(source.split("_")[1].split(".")[0])
                    rag_ids.append(pid)
                except (ValueError, IndexError):
                    pass

    print(f"\n2. Products in RAG (namespace='products'):")
    print(f"   Count: {len(rag_ids)}")
    print(f"   ID range: {min(rag_ids) if rag_ids else 'N/A'} - {max(rag_ids) if rag_ids else 'N/A'}")
    print(f"   Sample IDs: {sorted(rag_ids)[:10]}")

    # Check overlap
    catalog_set = set(catalog_ids)
    rag_set = set(rag_ids)

    overlap = catalog_set & rag_set
    only_catalog = catalog_set - rag_set
    only_rag = rag_set - catalog_set

    print(f"\n3. Overlap analysis:")
    print(f"   ✓ In both catalog and RAG: {len(overlap)}")
    print(f"   ⚠️  Only in catalog (not indexed): {len(only_catalog)}")
    if only_catalog:
        print(f"      Sample: {sorted(only_catalog)[:5]}")
    print(f"   ⚠️  Only in RAG (stale): {len(only_rag)}")
    if only_rag:
        print(f"      Sample: {sorted(only_rag)[:5]}")


if __name__ == "__main__":
    sys.exit(main())
