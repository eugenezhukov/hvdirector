"""FastAPI application (hvdirector-api)."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import text

from . import __version__
from .api import auth, common, console, edge, provider, provider_edge, provider_vms, tenant
from .config import get_config
from .db import SessionLocal
from .errors import AppError
from .hyperv import HyperVError, get_driver
from .logging_setup import setup_logging

log = logging.getLogger("hvdirector")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def _default_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    cfg = get_config()
    setup_logging(cfg.log_level)
    app = FastAPI(title="HV Director", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")

    for r in (auth.router, provider.router, provider_edge.router, provider_vms.router, tenant.router, edge.router,
              common.router, console.router):
        app.include_router(r)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_dict()})

    @app.exception_handler(HyperVError)
    async def hv_error(_: Request, exc: HyperVError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": {"code": "hyperv_error", "message": str(exc),
                                                                 "params": {"hv_code": exc.code}}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
        msg = "; ".join(f"{'.'.join(e['loc'][1:]) or e['loc']}: {e['msg']}" for e in errors)
        return JSONResponse(status_code=422, content={"detail": {"code": "validation_error", "message": msg,
                                                                 "params": {"errors": errors}}})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response: Response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/api/meta")
    def meta() -> dict:
        return {"product": "HV Director", "version": __version__, "driver": get_driver().name}

    @app.get("/api/health")
    def health() -> dict:
        db = SessionLocal()
        try:
            db.execute(text("select 1"))
        finally:
            db.close()
        return {"ok": True}

    dist = Path(cfg.frontend_dist) if cfg.frontend_dist else _default_dist()
    if dist.is_dir():
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith(("api/", "ws/")):
                return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "Not found",
                                                                         "params": {}}})
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{path}" else {}
                return FileResponse(candidate, headers=headers)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:
        log.warning("Frontend build not found at %s (API only)", dist)

    return app
# Run with: uvicorn --factory hvdirector.main:create_app
