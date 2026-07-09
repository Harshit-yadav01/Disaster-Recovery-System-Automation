"""FastAPI application entrypoint.

Runs the API and also serves the existing static frontend so the whole system
starts with a single command:

    uvicorn app.main:app --reload

Then open  http://127.0.0.1:8000/
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import auth, dashboard, dr

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="HPE DR Automation API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(dr.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": settings.storage_provider}


# --------------------------------------------------------------------------- #
# Serve the static frontend (Dashboard-DR) from the same server.
# The frontend lives one level up from the backend folder.
# --------------------------------------------------------------------------- #
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "Dashboard-DR"

if FRONTEND_DIR.is_dir():
    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # The login page is the entry point of the app.
        return RedirectResponse(url="/app/login.html")

    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    logging.getLogger("dr").warning("Frontend folder not found at %s", FRONTEND_DIR)
