#!/usr/bin/env python3
"""Index products into RAG for natural language search."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.session import SessionLocal
from app.rag.factory import build_embedder, build_vector_store
from app.rag.product_indexer import index_products


def main():
    print("Building RAG components...")
    embedder = build_embedder()
    if not embedder:
        print("Cảnh báo: không có embedder (thiếu OPENAI_API_KEY hoặc EMBEDDING_PROVIDER=none) -> chỉ lưu chunk cho BM25, không có vector")

    # Create store with 'products' namespace, not default 'knowledge'
    from app.db.session import engine
    from app.rag.pgstore import PgVectorStore
    store = PgVectorStore(engine, namespace='products')
    print(f"Using embedder: {embedder.name if embedder else 'none (BM25-only)'}")
    print(f"Using store: {store.__class__.__name__} (namespace: {store.ns})")

    db = SessionLocal()
    try:
        print("\nIndexing products...")
        result = index_products(db, embedder, store)
        print(f"✓ Indexed {result['chunks']} products")
        print(f"  Embedded: {result['embedded']} new/updated")
        print(f"  Deleted: {result['deleted']} old chunks")
        return 0
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
