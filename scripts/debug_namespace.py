#!/usr/bin/env python3
"""Debug namespace and actual query."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text as sql_text
from app.db.session import engine
from app.rag.factory import build_embedder
from app.rag.pgstore import _lit


def main():
    embedder = build_embedder()
    query = "xe rẻ, bền và tiết kiệm nhiên liệu"

    print("="*60)
    print("DEBUG: Namespace and Query")
    print("="*60)

    # Check what namespaces exist
    with engine.connect() as conn:
        namespaces = conn.execute(sql_text(
            "SELECT DISTINCT namespace, COUNT(*) FROM rag_chunks GROUP BY namespace"
        )).all()

        print("\nNamespaces in rag_chunks:")
        for ns, count in namespaces:
            print(f"  {ns}: {count} chunks")

    # Try both namespaces
    query_vec = embedder.embed_query(query)
    q = _lit(query_vec)

    for namespace in ['products', 'knowledge']:
        print(f"\n{'='*60}")
        print(f"Testing namespace: '{namespace}'")
        print(f"{'='*60}")

        sql = """SELECT source, LEFT(text, 150) as preview,
                        1 - (embedding <=> CAST(:q AS vector)) AS score
                 FROM rag_chunks
                 WHERE namespace=:ns AND kind='product' AND embedding IS NOT NULL
                 ORDER BY embedding <=> CAST(:q AS vector)
                 LIMIT 5"""

        with engine.connect() as conn:
            rows = conn.execute(sql_text(sql), {"q": q, "ns": namespace}).all()

        print(f"Found {len(rows)} results:")
        for i, (source, preview, score) in enumerate(rows, 1):
            print(f"{i}. {source} (score: {score:.3f})")
            print(f"   {preview}...")


if __name__ == "__main__":
    sys.exit(main())
