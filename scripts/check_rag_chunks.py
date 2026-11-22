#!/usr/bin/env python3
"""Check RAG chunks in database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db.session import engine


def main():
    with engine.connect() as conn:
        # Count all chunks
        total = conn.execute(text("SELECT COUNT(*) FROM rag_chunks")).scalar()
        print(f"Total chunks: {total}")

        # Count by kind
        print("\nChunks by kind:")
        rows = conn.execute(text("SELECT kind, COUNT(*) FROM rag_chunks GROUP BY kind")).all()
        for kind, count in rows:
            print(f"  {kind or '(null)'}: {count}")

        # Sample product chunks
        print("\nSample product chunks:")
        rows = conn.execute(text(
            "SELECT source, LEFT(text, 100) FROM rag_chunks WHERE kind='product' LIMIT 5"
        )).all()
        for source, chunk_text in rows:
            print(f"  {source}: {chunk_text}...")

        # Check embeddings
        print("\nEmbedding status:")
        with_emb = conn.execute(text("SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NOT NULL")).scalar()
        without_emb = conn.execute(text("SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NULL")).scalar()
        print(f"  With embeddings: {with_emb}")
        print(f"  Without embeddings: {without_emb}")


if __name__ == "__main__":
    sys.exit(main())
