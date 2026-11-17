"""Perfil de inquilino y preferencias de vivienda."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import catalog, crypto, forms
from . import criteria as criteria_lib
from .config import Settings
from .deps import audit, flash, get_db, get_settings, render, require_user, verify_csrf
from .models import Profile, SearchPreference, User

router = APIRouter()
ID_PURPOSE = "id_number"


def ensure_profile(user: User) -> Profile:
    if user.profile is None:
        user.profile = Profile()
    return user.profile


def ensure_preference(user: User) -> SearchPreference:
    if user.preference is None:
        user.preference = SearchPreference()
    return user.preference


def read_id_number(settings: Settings, user: User) -> str:
    blob = user.profile.id_number_enc if user.profile else None
    if not blob:
        return ""
    try:
        return crypto.decrypt(settings.secret_key, user.id, ID_PURPOSE, blob).decode()
    except crypto.DecryptionError:
        return ""


def profile_completion(user: User) -> list[dict]:
    """Pasos del perfil y si están hechos; lo usa el panel de inicio."""
    p, s = user.profile, user.preference
    return [
        {"label": "Datos personales", "href": "/perfil", "done": bool(p and p.phone and p.id_type)},
        {"label": "Situación laboral e ingresos", "href": "/perfil#trabajo",
         "done": bool(p and p.employment_status and p.net_monthly_income)},
        {"label": "Qué vivienda buscas", "href": "/busqueda", "done": bool(s and s.areas and s.max_price)},
        {"label": "Documentación", "href": "/documentos", "done": len(user.documents) > 0},
    ]


@router.get("/perfil")
def profile_form(request: Request, user: User = Depends(require_user), settings: Settings = Depends(get_settings)):
    return render(request, "profile.html", user=user, profile=ensure_profile(user),
                  id_number=read_id_number(settings, user))


@router.post("/perfil", dependencies=[Depends(verify_csrf)])
async def save_profile(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db),
                       settings: Settings = Depends(get_settings)):
    form = await request.form()
    profile = ensure_profile(user)
    try:
        values = {
            "phone": forms.text(form, "phone", 30),
            "nationality": forms.text(form, "nationality", 60),
            "birth_date": forms.date(form, "birth_date"),
            "id_type": forms.choice(form, "id_type", catalog.ID_TYPES),
            "employment_status": forms.choice(form, "employment_status", catalog.EMPLOYMENT),
            "employer": forms.text(form, "employer", 120),
            "job_title": forms.text(form, "job_title", 120),
            "employment_since": forms.date(form, "employment_since"),
            "net_monthly_income": forms.integer(form, "net_monthly_income", 0, 1_000_000),
            "other_monthly_income": forms.integer(form, "other_monthly_income", 0, 1_000_000),
            "has_guarantor": forms.flag(form, "has_guarantor"),
            "adults": forms.integer(form, "adults", 1, 12, default=1),
            "children": forms.integer(form, "children", 0, 12, default=0),
            "has_pets": forms.flag(form, "has_pets"),
            "pets_detail": forms.text(form, "pets_detail", 200),
            "smoker": forms.flag(form, "smoker"),
            "about": forms.text(form, "about", 1500),
        }
        name = forms.text(form, "name", 120, required=True)
        id_number = forms.text(form, "id_number", 30).upper()
    except forms.FormError as exc:
        return render(request, "profile.html", user=user, profile=profile, status_code=400, error=str(exc),
                      id_number=str(form.get("id_number") or ""))
    for field, value in values.items():
        setattr(profile, field, value)
    user.name = name
    profile.id_number_enc = (
        crypto.encrypt(settings.secret_key, user.id, ID_PURPOSE, id_number.encode()) if id_number else None
    )
    audit(db, request, user.id, "perfil_actualizado")
    flash(request, "Perfil guardado.")
    return RedirectResponse("/perfil", status_code=303)


@router.get("/busqueda")
def preference_form(request: Request, user: User = Depends(require_user)):
    return render(request, "preferences.html", user=user, pref=ensure_preference(user))


@router.post("/busqueda", dependencies=[Depends(verify_csrf)])
async def save_preference(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    pref = ensure_preference(user)
    try:
        values = {
            **criteria_lib.read_form(form),
            "move_in_date": forms.date(form, "move_in_date"),
            "notes": forms.text(form, "notes", 1000),
        }
    except forms.FormError as exc:
        return render(request, "preferences.html", user=user, pref=pref, status_code=400, error=str(exc))
    for field, value in values.items():
        setattr(pref, field, value)
    audit(db, request, user.id, "busqueda_actualizada")
    flash(request, "Preferencias guardadas. Estos son los pisos que encajan.")
    return RedirectResponse("/pisos", status_code=303)
