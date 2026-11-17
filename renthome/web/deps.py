"""Dependencias compartidas por las rutas: sesión de BD, usuario, CSRF, plantillas."""
from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from . import catalog
from .config import Settings
from .db import session_scope
from .models import AuditLog, User

FLASH_KEY = "_flash"


class LoginRequired(Exception):
    """La ruta necesita sesión iniciada."""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


def client_ip(request: Request) -> str:
    # Detrás del túnel, Cloudflare envía la IP real en esta cabecera
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("cf-connecting-ip") if request.app.state.settings.trust_cf_header else None
    return (forwarded or peer)[:45]


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active or user.session_version != request.session.get("sv"):
        request.session.clear()
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired()
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=404)
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    form = await request.form(max_files=2, max_fields=60)
    sent = str(form.get("csrf_token") or "")
    expected = request.session.get("csrf") or ""
    if not expected or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Formulario caducado. Vuelve atrás y recarga la página.")


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session[FLASH_KEY] = [*request.session.get(FLASH_KEY, []), {"kind": kind, "text": message}]


def audit(db: Session, request: Request, user_id: int | None, action: str, detail: str = "") -> None:
    db.add(AuditLog(user_id=user_id, action=action, detail=detail[:200], ip=client_ip(request)))


def render(request: Request, template: str, user: User | None = None, status_code: int = 200, **context) -> HTMLResponse:
    messages = request.session.pop(FLASH_KEY, [])
    base = {
        "user": user,
        "csrf_token": csrf_token(request),
        "messages": messages,
        "catalog": catalog,
        "path": request.url.path,
        # sin correo de envío configurado no se ofrecen avisos por email
        "email_enabled": getattr(request.app.state, "mailer", None) is not None,
    }
    return request.app.state.templates.TemplateResponse(request, template, {**base, **context}, status_code=status_code)
