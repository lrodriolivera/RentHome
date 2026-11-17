"""Aplicación FastAPI de RentHome.

    uvicorn renthome.web.app:create_app --factory --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import (agencies, routes_admin, routes_agencies, routes_auth, routes_documents, routes_home, routes_privacy,
               routes_profile, routes_search)
from .config import SESSION_MAX_AGE_SECONDS, Settings, load_settings
from .db import Base, make_engine, make_session_factory, session_scope
from .deps import LoginRequired, render
from .limits import BodyLimitMiddleware
from .routes_privacy import purge_stale
from .security import LoginLimiter
from .worker import CrawlWorker

PURGE_INTERVAL_SECONDS = 24 * 3600
HERE = Path(__file__).resolve().parent
log = logging.getLogger("renthome.web")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; "
        "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Robots-Tag": "noindex, nofollow",
}

ERROR_TITLES = {403: "Acceso no permitido", 404: "Página no encontrada", 410: "Ya no está disponible"}


def _format_date(value) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def _format_datetime(value) -> str:
    return value.strftime("%d/%m/%Y %H:%M") if value else ""


def _format_money(value: int | None) -> str:
    return f"{value:,}".replace(",", ".") + " €" if value is not None else ""


def _format_size(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MB" if value >= 1024 * 1024 else f"{max(1, value // 1024)} KB"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)

    session_factory = make_session_factory(engine)
    for db in session_scope(session_factory):
        added = agencies.seed(db)
    if added:
        log.info("Añadidas %d inmobiliarias iniciales", added)
    worker = CrawlWorker(session_factory)

    def run_purge() -> None:
        try:
            for db in session_scope(session_factory):
                removed = purge_stale(db, settings)
            if removed:
                log.info("Eliminadas %d cuentas inactivas", removed)
        except Exception:  # la limpieza no debe tumbar el servidor
            log.exception("Fallo en la limpieza de cuentas inactivas")

    async def purge_loop() -> None:
        while True:
            await asyncio.to_thread(run_purge)
            await asyncio.sleep(PURGE_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(purge_loop())
        if settings.crawl_enabled:
            worker.start()
        yield
        worker.stop()
        task.cancel()

    app = FastAPI(title="RentHome", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.worker = worker
    app.state.limiter = LoginLimiter()

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(date=_format_date, datetime=_format_datetime, money=_format_money,
                                 filesize=_format_size)
    app.state.templates = templates

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Se añade después para que envuelva al middleware anterior y la sesión esté disponible
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="renthome_session",
                       max_age=SESSION_MAX_AGE_SECONDS, same_site="lax", https_only=settings.cookie_secure)
    app.add_middleware(BodyLimitMiddleware)  # el más externo: corta antes de leer el cuerpo

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, _exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        title = ERROR_TITLES.get(exc.status_code, "Algo no ha ido bien")
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else ""
        return render(request, "error.html", status_code=exc.status_code, title=title, detail=detail)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Error no controlado en %s", request.url.path, exc_info=exc)
        response = render(request, "error.html", status_code=500, title="Algo no ha ido bien",
                          detail="Ha ocurrido un error inesperado. Inténtalo de nuevo en un momento.")
        # este manejador corre fuera del middleware de cabeceras
        response.headers.update({**SECURITY_HEADERS, "Cache-Control": "no-store"})
        return response

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    for module in (routes_home, routes_auth, routes_profile, routes_documents, routes_privacy, routes_search,
                   routes_admin, routes_agencies):
        app.include_router(module.router)
    return app
