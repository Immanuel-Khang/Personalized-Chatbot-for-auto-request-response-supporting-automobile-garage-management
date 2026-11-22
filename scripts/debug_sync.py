#!/usr/bin/env python3
"""Debug what sync() is actually doing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text as sql_text
from app.db.session import engine, SessionLocal
from app.rag.factory import build_embedder
from app.rag.pgstore import PgVectorStore
from app.rag.product_indexer import extract_product_chunks


def main():
    print("="*60)
    print("DEBUG: What sync() receives and does")
    print("="*60)

    # Extract chunks from current catalog
    with SessionLocal() as db:
        chunks = extract_product_chunks(db)

    print(f"\n1. extract_product_chunks() returned {len(chunks)} chunks:")
    print(f"   Sample sources: {[c.source for c in chunks[:5]]}")
    print(f"   Sample IDs: {[c.id for c in chunks[:5]]}")

    # Check what's currently in RAG
    with engine.connect() as conn:
        current = conn.execute(sql_text(
            "SELECT source, id FROM rag_chunks WHERE namespace='products' AND kind='product' ORDER BY source LIMIT 10"
        )).all()

    print(f"\n2. Currently in RAG: {len(current)} chunks")
    if current:
        print(f"   Sample: {current[:5]}")

    # Check if chunk IDs match
    chunk_ids = set(c.id for c in chunks)
    current_ids = set(row[1] for row in current) if current else set()

    overlap = chunk_ids & current_ids
    only_new = chunk_ids - current_ids
    only_old = current_ids - chunk_ids

    print(f"\n3. ID overlap:")
    print(f"   ✓ In both: {len(overlap)}")
    print(f"   + Only in new chunks: {len(only_new)}")
    if only_new:
        # Find which sources these belong to
        new_sources = [c.source for c in chunks if c.id in list(only_new)[:3]]
        print(f"      Sample sources: {new_sources}")
    print(f"   - Only in old RAG: {len(only_old)}")

    print(f"\n4. What sync() would do:")
    print(f"   - It will upsert {len(chunks)} chunks")
    print(f"   - It will DELETE chunks where id NOT IN ({len(chunk_ids)} IDs)")
    print(f"   - Expected deletions: {len(only_old)}")


if __name__ == "__main__":
    sys.exit(main())
