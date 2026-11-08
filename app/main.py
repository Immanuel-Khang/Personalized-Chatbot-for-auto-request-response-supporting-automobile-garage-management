from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, chat, staff_actions
from app.db.seed import seed_catalog
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_catalog()
    yield


app = FastAPI(title="Xuong xe AI Agent", lifespan=lifespan)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(staff_actions.router)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    """Trang admin ở /admin (StaticFiles chỉ phục vụ /admin.html)."""
    return FileResponse(WEB_DIR / "admin.html")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
