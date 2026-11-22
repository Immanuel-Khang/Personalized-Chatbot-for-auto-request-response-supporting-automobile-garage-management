#!/usr/bin/env python3
"""Test RAG product search with Vietnamese queries."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.product import DbProductService


def main():
    service = DbProductService()

    queries = [
        "tìm xe rẻ và bền",
        "xe tiết kiệm nhiên liệu",
        "xe tải chở hàng nặng",
        "xe phù hợp chạy đường dài",
        "xe giá tốt cho kinh doanh vận tải",
    ]

    print("Testing RAG product search with Vietnamese queries\n")

    for query in queries:
        print(f"{'='*60}")
        print(f"Query: {query}")
        print('='*60)

        products = service.search_by_rag(query, limit=3)

        if not products:
            print("No products found.\n")
            continue

        for i, p in enumerate(products, 1):
            print(f"\n{i}. {p.name}")
            print(f"   ID: {p.id}")
            print(f"   Price: {p.list_price_vnd:,} VND")
            if p.sales_info and "summary" in p.sales_info:
                summary = p.sales_info["summary"][:150]
                print(f"   Summary: {summary}...")
        print()

    print(f"{'='*60}")
    print("✓ Test complete")


if __name__ == "__main__":
    sys.exit(main())
