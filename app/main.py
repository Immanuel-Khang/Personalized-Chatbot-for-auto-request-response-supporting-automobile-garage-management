from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, chat
from app.db.seed import seed_catalog
from app.db.session import init_db


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
    yield


app = FastAPI(title="Xuong xe AI Agent", lifespan=lifespan)
app.include_router(chat.router)
app.include_router(admin.router)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    """Trang admin ở /admin (StaticFiles chỉ phục vụ /admin.html)."""
    return FileResponse(WEB_DIR / "admin.html")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
