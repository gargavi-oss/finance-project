"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api import auth_router, documents_router, system_router
from app.config import get_settings
from app.db.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.data_dir) / "uploads").mkdir(parents=True, exist_ok=True)
    (Path(settings.data_dir) / "overlays").mkdir(parents=True, exist_ok=True)
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DocForensic AI",
        version="2.0.4",
        description="Six-agent fraud detection pipeline for invoices and receipts.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")

    # Serve uploaded images + ELA overlays for the frontend.
    data_dir = Path(settings.data_dir).resolve()
    app.mount("/data", StaticFiles(directory=data_dir), name="data")

    # Optional single-origin deploy: if the React app has been built, serve
    # its static bundle from the project root's frontend/dist (or env override)
    # so `uv run uvicorn app.main:app` is the only command a reviewer needs.
    dist = Path(getattr(settings, "frontend_dist", "") or "../frontend/dist").resolve()
    if dist.is_dir():
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}")
        async def _spa(full_path: str):  # noqa: ANN001
            target = dist / full_path
            if full_path and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()