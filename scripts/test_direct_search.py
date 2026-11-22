#!/usr/bin/env python3
"""Test direct pgvector search for products."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db.session import engine
from app.rag.factory import build_embedder
from app.rag.pgstore import _lit


def main():
    embedder = build_embedder()
    if not embedder:
        print("No embedder available")
        return 1

    query = "tìm xe rẻ và bền"
    print(f"Query: {query}")
    print(f"Embedder: {embedder.name}\n")

    # Embed query
    query_vec = embedder.embed_query(query)
    print(f"Query vector dimension: {len(query_vec)}\n")

    # Search for products
    q = _lit(query_vec)
    sql = """SELECT source, LEFT(text, 150) AS preview,
                    1 - (embedding <=> CAST(:q AS vector)) AS score
             FROM rag_chunks
             WHERE namespace='knowledge' AND kind='product' AND embedding IS NOT NULL
             ORDER BY embedding <=> CAST(:q AS vector)
             LIMIT 5"""

    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"q": q}).all()

    print(f"Found {len(rows)} results:\n")
    for i, (source, preview, score) in enumerate(rows, 1):
        print(f"{i}. {source} (score: {score:.3f})")
        print(f"   {preview}...")
        print()


if __name__ == "__main__":
    sys.exit(main())
