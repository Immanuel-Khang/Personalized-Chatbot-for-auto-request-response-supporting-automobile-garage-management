from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import admin, chat
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
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="web")
