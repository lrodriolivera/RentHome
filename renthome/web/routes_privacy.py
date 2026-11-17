"""Derechos RGPD: información, acceso/portabilidad y supresión."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from typing import IO

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crypto, documents, security
from .config import AUDIT_RETENTION_DAYS, INACTIVE_DAYS, PRIVACY_VERSION, Settings
from .deps import audit, current_user, flash, get_db, get_settings, render, require_user, verify_csrf
from .models import AuditLog, Invitation, Listing, User, utcnow
from .routes_profile import read_id_number

router = APIRouter()
EXPORT_CHUNK_BYTES = 256 * 1024
EXPORT_SPOOL_BYTES = 8 * 1024 * 1024
# las exportaciones descifran documentos: como mucho dos a la vez
_export_slots = threading.BoundedSemaphore(2)


def _columns(obj, skip: tuple[str, ...] = ()) -> dict:
    if obj is None:
        return {}
    return {c.name: (v.isoformat() if hasattr(v, "isoformat") else v)
            for c in obj.__table__.columns
            if c.name not in skip and not isinstance(v := getattr(obj, c.name), bytes)}


def _draft(settings: Settings, state) -> str:
    if not state.draft_message_enc:
        return ""
    try:
        return crypto.decrypt(settings.secret_key, state.user_id, "draft", state.draft_message_enc).decode()
    except crypto.DecryptionError:
        return ""


def build_export(db: Session, settings: Settings, user: User, out: IO[bytes]) -> None:
    """Escribe en `out` un ZIP con los datos del usuario en JSON y sus documentos descifrados."""
    log = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.at)).all()
    data = {
        "exportado_por": "RentHome",
        "version_privacidad_aceptada": user.consent_version,
        "cuenta": _columns(user, skip=("password_hash", "session_version")),
        "perfil": {**_columns(user.profile), "id_number": read_id_number(settings, user)},
        "busqueda": _columns(user.preference),
        "documentos": [_columns(d, skip=("storage_name",)) for d in user.documents],
        "busquedas_guardadas": [_columns(s) for s in user.saved_searches],
        "anuncios_guardados_o_descartados": [
            {**_columns(state), "anuncio": getattr(db.get(Listing, state.listing_id), "url", ""),
             "borrador_mensaje": _draft(settings, state)}
            for state in user.listing_states],
        "registro_de_actividad": [_columns(entry) for entry in log],
    }
    # sin compresión: los PDF y las imágenes ya vienen comprimidos
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("mis-datos.json", json.dumps(data, ensure_ascii=False, indent=2))
        for doc in user.documents:
            try:
                archive.writestr(f"documentos/{doc.id}-{doc.filename}", documents.read(settings, doc))
            except (crypto.DecryptionError, FileNotFoundError):
                continue


def _stream_and_release(spool: IO[bytes]) -> Iterator[bytes]:
    try:
        spool.seek(0)
        while chunk := spool.read(EXPORT_CHUNK_BYTES):
            yield chunk
    finally:
        spool.close()
        _export_slots.release()


def erase_user(db: Session, settings: Settings, user: User) -> None:
    """Borra la cuenta y todo lo asociado; el registro de actividad queda anónimo."""
    for doc in user.documents:
        documents.remove(settings, doc)
    for entry in db.scalars(select(AuditLog).where(AuditLog.user_id == user.id)):
        entry.user_id = None
        entry.ip = ""
    db.delete(user)


def purge_stale(db: Session, settings: Settings) -> int:
    """Limitación del plazo de conservación: borra cuentas inactivas e invitaciones caducadas."""
    cutoff = utcnow() - dt.timedelta(days=INACTIVE_DAYS)
    stale = [u for u in db.scalars(select(User).where(User.role != "admin"))
             if (u.last_login_at or u.created_at) < cutoff]
    for user in stale:
        erase_user(db, settings, user)
        db.add(AuditLog(user_id=None, action="cuenta_eliminada", detail="por inactividad"))
    for inv in db.scalars(select(Invitation).where(Invitation.expires_at < utcnow())):
        db.delete(inv)
    audit_cutoff = utcnow() - dt.timedelta(days=AUDIT_RETENTION_DAYS)
    for entry in db.scalars(select(AuditLog).where(AuditLog.at < audit_cutoff)):
        db.delete(entry)
    return len(stale)


@router.get("/privacidad")
def privacy_policy(request: Request, user: User | None = Depends(current_user),
                   settings: Settings = Depends(get_settings)):
    return render(request, "privacy.html", user=user, settings=settings, version=PRIVACY_VERSION)


@router.get("/mis-datos")
def my_data(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    log = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.at.desc()).limit(30)).all()
    return render(request, "my_data.html", user=user, log=log)


@router.post("/mis-datos/exportar", dependencies=[Depends(verify_csrf)])
def export_my_data(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    if not _export_slots.acquire(blocking=False):
        flash(request, "Hay otras descargas en curso. Inténtalo de nuevo en un minuto.", "error")
        return RedirectResponse("/mis-datos", status_code=303)
    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_BYTES)
    try:
        build_export(db, settings, user, spool)
    except Exception:
        spool.close()
        _export_slots.release()
        raise
    audit(db, request, user.id, "datos_exportados")
    headers = {"Content-Disposition": 'attachment; filename="renthome-mis-datos.zip"', "Cache-Control": "no-store"}
    return StreamingResponse(_stream_and_release(spool), media_type="application/zip", headers=headers)


@router.post("/mis-datos/eliminar", dependencies=[Depends(verify_csrf)])
async def delete_my_account(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
                            settings: Settings = Depends(get_settings)):
    form = await request.form()
    limiter, key = request.app.state.limiter, f"user:{user.id}"
    if limiter.is_blocked(key):
        flash(request, "Demasiados intentos. Espera 15 minutos.", "error")
        return RedirectResponse("/mis-datos", status_code=303)
    if not security.verify_password(user.password_hash, str(form.get("password") or "")):
        limiter.record_failure(key)
        flash(request, "La contraseña no es correcta; no se ha borrado nada.", "error")
        return RedirectResponse("/mis-datos", status_code=303)
    if user.is_admin and db.scalar(select(User).where(User.role == "admin", User.id != user.id)) is None:
        flash(request, "Eres el único administrador: crea otro antes de borrar tu cuenta.", "error")
        return RedirectResponse("/mis-datos", status_code=303)
    audit(db, request, None, "cuenta_eliminada", "a petición del usuario")
    erase_user(db, settings, user)
    request.session.clear()
    return RedirectResponse("/login?eliminada=1", status_code=303)
