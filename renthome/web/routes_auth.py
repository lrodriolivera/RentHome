"""Acceso, invitaciones y cambio de contraseña."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import forms, security
from .config import PRIVACY_VERSION
from .deps import audit, client_ip, current_user, flash, get_db, render, require_user, verify_csrf
from .models import Invitation, Profile, SearchPreference, User, new_session_version, utcnow

router = APIRouter()


def start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version


def find_invitation(db: Session, token: str) -> Invitation | None:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == security.token_hash(token)))
    if inv is None or inv.used_at is not None or inv.expires_at < utcnow():
        return None
    return inv


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = str(form.get("email") or "").strip().lower()[:security.MAX_EMAIL_LENGTH]
    password = str(form.get("password") or "")[:security.MAX_PASSWORD_LENGTH + 1]
    limiter = request.app.state.limiter
    keys = (f"ip:{client_ip(request)}", f"email:{email}")
    if limiter.is_blocked(*keys):
        return render(request, "login.html", status_code=429, email=email,
                      error="Demasiados intentos. Espera 15 minutos y vuelve a probar.")
    user = db.scalar(select(User).where(User.email == email))
    ok = security.verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        limiter.record_failure(*keys)
        if user is not None:
            audit(db, request, user.id, "login_fallido")
        return render(request, "login.html", status_code=401, email=email,
                      error="Email o contraseña incorrectos.")
    limiter.reset(*keys)
    user.last_login_at = utcnow()
    audit(db, request, user.id, "login")
    start_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        user.session_version = new_session_version()  # invalida también copias de la cookie
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/invitacion/{token}")
def invitation_form(token: str, request: Request, db: Session = Depends(get_db)):
    inv = find_invitation(db, token)
    if inv is None:
        return render(request, "invitation_invalid.html", status_code=404)
    return render(request, "invitation.html", invitation=inv, token=token)


@router.post("/invitacion/{token}", dependencies=[Depends(verify_csrf)])
async def accept_invitation(token: str, request: Request, db: Session = Depends(get_db)):
    inv = find_invitation(db, token)
    if inv is None:
        return render(request, "invitation_invalid.html", status_code=404)
    form = await request.form()
    password = str(form.get("password") or "")
    error = None
    try:
        name = forms.text(form, "name", 120, required=True)
    except forms.FormError as exc:
        name, error = "", str(exc)
    error = error or security.password_problem(password)
    if not error and password != str(form.get("password2") or ""):
        error = "Las dos contraseñas no coinciden."
    if not error and not forms.flag(form, "consent"):
        error = "Para crear la cuenta necesitamos que aceptes la política de privacidad."
    if not error and db.scalar(select(User).where(User.email == inv.email)):
        error = "Ya existe una cuenta con este email. Inicia sesión."
    if error:
        return render(request, "invitation.html", status_code=400, invitation=inv, token=token, error=error, name=name)

    user = User(email=inv.email, name=name, password_hash=security.hash_password(password),
                consent_at=utcnow(), consent_version=PRIVACY_VERSION)
    user.profile = Profile()
    user.preference = SearchPreference()
    db.add(user)
    inv.used_at = utcnow()
    try:
        db.flush()
    except IntegrityError:  # dos envíos simultáneos de la misma invitación
        db.rollback()
        return render(request, "invitation_invalid.html", status_code=409)
    audit(db, request, user.id, "cuenta_creada", f"consentimiento {PRIVACY_VERSION}")
    start_session(request, user)
    flash(request, "Cuenta creada. Completa tu perfil para que las búsquedas se ajusten a ti.")
    return RedirectResponse("/perfil", status_code=303)


@router.get("/cuenta")
def account(request: Request, user: User = Depends(require_user)):
    return render(request, "account.html", user=user)


@router.post("/cuenta/contrasena", dependencies=[Depends(verify_csrf)])
async def change_password(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    new = str(form.get("password") or "")
    error = None
    limiter, key = request.app.state.limiter, f"user:{user.id}"
    if limiter.is_blocked(key):
        error = "Demasiados intentos. Espera 15 minutos."
    elif not security.verify_password(user.password_hash, str(form.get("current") or "")):
        limiter.record_failure(key)
        error = "La contraseña actual no es correcta."
    error = error or security.password_problem(new)
    if not error and new != str(form.get("password2") or ""):
        error = "Las dos contraseñas nuevas no coinciden."
    if error:
        return render(request, "account.html", user=user, status_code=400, error=error)
    user.password_hash = security.hash_password(new)
    user.session_version = new_session_version()  # cierra las demás sesiones abiertas
    audit(db, request, user.id, "contrasena_cambiada")
    db.flush()
    start_session(request, user)
    flash(request, "Contraseña cambiada. Se han cerrado tus otras sesiones.")
    return RedirectResponse("/cuenta", status_code=303)
