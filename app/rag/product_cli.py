"""CLI để quản lý product RAG index.

Usage:
    python -m app.rag.product_cli sync    # Đồng bộ products vào rag_chunks
    python -m app.rag.product_cli search "xe chở hàng nặng đường đèo" --k 5
"""
import argparse
import logging
import sys

from app.config import settings
from app.db.session import engine
from app.rag.factory import build_embedder
from app.rag.product_retriever import ProductRetriever
from app.services.product import DbProductService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def sync_command(args):
    """Đồng bộ products vào RAG index."""
    embedder = build_embedder()
    if not embedder:
        logger.error("No embedder available. Set EMBEDDING_PROVIDER and OPENAI_API_KEY in .env")
        return 1

    logger.info("Using embedder: %s", embedder.name)
    retriever = ProductRetriever(engine, embedder)

    try:
        result = retriever.sync()
        logger.info("Sync completed: %s", result)
        return 0
    except Exception as e:
        logger.error("Sync failed: %s", e, exc_info=True)
        return 1


def search_command(args):
    """Test product search với RAG."""
    service = DbProductService()

    logger.info("Searching for: %s (k=%d, budget=%s)", args.query, args.k, args.budget)

    try:
        products = service.retrieve(
            query=args.query,
            k=args.k,
            max_budget_vnd=float(args.budget) if args.budget else None
        )

        if not products:
            print("No products found.")
            return 0

        print(f"\nFound {len(products)} products:\n")
        for i, p in enumerate(products, 1):
            print(f"{i}. {p.name}")
            print(f"   ID: {p.id}")
            print(f"   Giá: {p.list_price_vnd:,} VNĐ (áp dụng từ {p.price_as_of})")

            specs = p.specs or {}
            if specs.get("payload_kg"):
                print(f"   Tải trọng: {specs['payload_kg']} kg")
            if specs.get("wheelbase_mm"):
                print(f"   Chiều dài cơ sở: {specs['wheelbase_mm']} mm")
            if specs.get("engine"):
                print(f"   Động cơ: {specs['engine']}")

            # Sales info highlights
            sales_info = p.sales_info or {}
            if sales_info.get("suitable_use"):
                print(f"   Phù hợp: {sales_info['suitable_use']}")

            print()

        return 0
    except Exception as e:
        logger.error("Search failed: %s", e, exc_info=True)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Product RAG CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Đồng bộ products vào RAG index")

    # search command
    search_parser = subparsers.add_parser("search", help="Test product search")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--k", type=int, default=5, help="Number of results")
    search_parser.add_argument("--budget", help="Max budget in VND (e.g., 500000000)")

    args = parser.parse_args()

    if args.command == "sync":
        return sync_command(args)
    elif args.command == "search":
        return search_command(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
