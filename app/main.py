from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, chat
from app.db.seed import seed_catalog
from app.db.session import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-run migrations on startup
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    alembic_cfg = Config(Path(__file__).parent.parent / "alembic.ini")
    command.upgrade(alembic_cfg, "head")

    init_db()
    seed_catalog()
    # Build the process-local product BM25 index and establish the embedding client
    # before the first customer request pays that initialization cost.
    try:
        from app.services import get_services
        get_services().product.warm_rag_search()
    except Exception:
        # Retrieval can still fall back to its normal lazy initialization on demand.
        logger.warning("product RAG startup warm-up failed; it will initialize on first search", exc_info=True)
    yield


app = FastAPI(title="Xuong xe AI Agent", lifespan=lifespan)
app.include_router(chat.router)
app.include_router(admin.router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    """Trạng thái app + độ khớp chỉ mục RAG sản phẩm. Lệch (degraded) = bot sẽ nói 'chưa lọc ra được mẫu nào'."""
    from app.db.session import SessionLocal
    from app.rag.product_indexer import check_product_index
    with SessionLocal() as db:
        idx = check_product_index(db)
    return {"status": "ok" if idx["in_sync"] else "degraded", "product_index": idx}


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    """Trang admin ở /admin (StaticFiles chỉ phục vụ /admin.html)."""
    return FileResponse(WEB_DIR / "admin.html")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
