#!/usr/bin/env python3
"""Test natural language search for products using live LLM and DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.session import SessionLocal
from app.rag.factory import build_retriever


def test_query(query: str):
    """Test a search query and display results."""
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print('='*60)

    retriever = build_retriever()
    results = retriever.retrieve(query, k=5)

    if not results:
        print("No results found.")
        return

    for i, result in enumerate(results, 1):
        print(f"\n{i}. {result.source}")
        print(f"   Score: {result.score:.3f}")
        print(f"   Text: {result.text[:200]}...")


def main():
    db = SessionLocal()
    try:
        # Vietnamese queries for car sales
        queries = [
            "tìm xe rẻ và bền",
            "xe tiết kiệm nhiên liệu",
            "xe tải chở hàng nặng",
            "xe phù hợp chạy đường dài",
            "xe giá tốt cho kinh doanh vận tải",
        ]

        print("Testing product search with natural language queries")
        print("Using live LLM embeddings and database")

        for query in queries:
            test_query(query)

        print(f"\n{'='*60}")
        print("✓ Test complete")

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
