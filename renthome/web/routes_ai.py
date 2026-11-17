"""Asistente con IA local para un piso concreto: encaje con el perfil y mensaje a la inmobiliaria."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import ai_tenant, crypto
from .ai import LLMUnavailable
from .config import Settings
from .deps import audit, flash, get_db, get_settings, render, require_user, verify_csrf
from .models import Listing, User, UserListing

router = APIRouter()
DRAFT_PURPOSE = "draft"
# el modelo atiende una petición cada vez; si está ocupado se avisa en lugar de apilar esperas
_assistant_busy = threading.Lock()


def get_listing(db: Session, listing_id: int) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404)
    return listing


def user_state(db: Session, user: User, listing_id: int, create: bool = False) -> UserListing | None:
    state = db.get(UserListing, (user.id, listing_id))
    if state is None and create:
        state = UserListing(user_id=user.id, listing_id=listing_id, state="saved")
        db.add(state)
    return state


def read_draft(settings: Settings, state: UserListing | None) -> str:
    if state is None or not state.draft_message_enc:
        return ""
    try:
        return crypto.decrypt(settings.secret_key, state.user_id, DRAFT_PURPOSE, state.draft_message_enc).decode()
    except crypto.DecryptionError:
        return ""


def write_draft(settings: Settings, state: UserListing, text: str) -> None:
    state.draft_message_enc = (
        crypto.encrypt(settings.secret_key, state.user_id, DRAFT_PURPOSE, text.encode()) if text else None)


def back_to(listing_id: int) -> RedirectResponse:
    return RedirectResponse(f"/pisos/{listing_id}/mensaje", status_code=303)


@router.get("/pisos/{listing_id}/mensaje")
def assistant(listing_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    listing = get_listing(db, listing_id)
    state = user_state(db, user, listing_id)
    return render(request, "assistant.html", user=user, listing=listing, state=state,
                  draft=read_draft(settings, state), ai_ready=request.app.state.llm is not None,
                  languages=ai_tenant.LANGUAGES,
                  brief=ai_tenant.tenant_brief(user, user.profile, user.preference, True))


def _run_assistant(request: Request, listing_id: int, action):
    """Ejecuta una llamada al modelo con un único turno a la vez y errores legibles."""
    llm = request.app.state.llm
    if llm is None:
        flash(request, "El asistente de IA no está configurado en este servidor.", "error")
        return back_to(listing_id)
    if not _assistant_busy.acquire(blocking=False):
        flash(request, "El asistente está ocupado con otra petición. Prueba en un minuto.", "error")
        return back_to(listing_id)
    try:
        action(llm)
    except LLMUnavailable:
        flash(request, "El asistente no ha respondido. Inténtalo de nuevo en un momento.", "error")
    finally:
        _assistant_busy.release()
    return back_to(listing_id)


@router.post("/pisos/{listing_id}/encaje", dependencies=[Depends(verify_csrf)])
def score_fit(listing_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    listing = get_listing(db, listing_id)

    def action(llm) -> None:
        score, reason = ai_tenant.fit(llm, user, listing)
        state = user_state(db, user, listing_id, create=True)
        state.fit_score, state.fit_reason = score, reason
        audit(db, request, user.id, "ia_encaje")

    return _run_assistant(request, listing_id, action)


@router.post("/pisos/{listing_id}/mensaje/generar", dependencies=[Depends(verify_csrf)])
async def generate_message(listing_id: int, request: Request, user: User = Depends(require_user),
                           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    listing = get_listing(db, listing_id)
    form = await request.form()
    language = str(form.get("language") or "es")

    def action(llm) -> None:
        text = ai_tenant.draft_message(llm, user, listing, language)
        write_draft(settings, user_state(db, user, listing_id, create=True), text)
        audit(db, request, user.id, "ia_mensaje")
        flash(request, "Borrador listo. Revísalo y adáptalo antes de enviarlo.")

    return _run_assistant(request, listing_id, action)


@router.post("/pisos/{listing_id}/mensaje/guardar", dependencies=[Depends(verify_csrf)])
async def save_message(listing_id: int, request: Request, user: User = Depends(require_user),
                       db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    get_listing(db, listing_id)
    form = await request.form()
    text = str(form.get("message") or "").strip()[:ai_tenant.MAX_MESSAGE_CHARS]
    write_draft(settings, user_state(db, user, listing_id, create=True), text)
    flash(request, "Mensaje guardado." if text else "Mensaje borrado.")
    return back_to(listing_id)
