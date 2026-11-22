#!/usr/bin/env python3
"""Check what content is actually in RAG product chunks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text as sql_text
from app.db.session import engine


def main():
    with engine.connect() as conn:
        # Get all product chunks
        rows = conn.execute(sql_text(
            """SELECT source, text, namespace, kind
               FROM rag_chunks
               WHERE kind='product'
               ORDER BY source
               LIMIT 10"""
        )).all()

        print("="*60)
        print("PRODUCT CHUNK CONTENT ANALYSIS")
        print("="*60)

        for source, text, namespace, kind in rows:
            print(f"\nSource: {source}")
            print(f"Namespace: {namespace}")
            print(f"Kind: {kind}")
            print(f"Content length: {len(text)} chars")
            print(f"Content preview:\n{text[:500]}...")
            print("-"*60)

        # Check if any contain keywords
        print("\n" + "="*60)
        print("KEYWORD SEARCH IN CHUNKS")
        print("="*60)

        keywords = ["rẻ", "bền", "tiết kiệm", "nhiên liệu", "giá tốt", "kinh tế", "chất lượng"]

        for keyword in keywords:
            count = conn.execute(sql_text(
                f"""SELECT COUNT(*) FROM rag_chunks
                   WHERE kind='product' AND text ILIKE :pattern"""
            ), {"pattern": f"%{keyword}%"}).scalar()

            print(f"Keyword '{keyword}': {count} chunks")


if __name__ == "__main__":
    sys.exit(main())
