"""Panel de administrador: invitaciones, usuarios y registro de actividad.

El administrador gestiona cuentas, pero no tiene ninguna ruta para ver perfiles
ni documentos de otros usuarios.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import forms, security
from .config import INVITATION_DAYS, Settings
from .deps import audit, flash, get_db, get_settings, render, require_admin, verify_csrf
from .models import AuditLog, Document, Invitation, User, new_session_version, utcnow
from .routes_privacy import erase_user

router = APIRouter(prefix="/admin")


def other_user(db: Session, admin: User, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Esta acción no se puede hacer sobre tu propia cuenta.")
    return target


@router.get("/usuarios")
def users(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    all_users = db.scalars(select(User).order_by(User.created_at)).all()
    doc_counts = dict(db.execute(select(Document.user_id, func.count()).group_by(Document.user_id)).all())
    pending = db.scalars(
        select(Invitation).where(Invitation.used_at.is_(None), Invitation.expires_at > utcnow())
        .order_by(Invitation.created_at.desc())
    ).all()
    new_link = request.session.pop("new_invitation", None)
    return render(request, "admin_users.html", user=admin, users=all_users, doc_counts=doc_counts,
                  invitations=pending, new_link=new_link, invitation_days=INVITATION_DAYS)


@router.post("/invitaciones", dependencies=[Depends(verify_csrf)])
async def invite(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        email = forms.email(form)
    except forms.FormError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/usuarios", status_code=303)
    if db.scalar(select(User).where(User.email == email)):
        flash(request, "Ya existe una cuenta con ese email.", "error")
        return RedirectResponse("/admin/usuarios", status_code=303)
    # una sola invitación viva por email
    for old in db.scalars(select(Invitation).where(Invitation.email == email, Invitation.used_at.is_(None))):
        db.delete(old)
    token = security.new_token()
    db.add(Invitation(email=email, token_hash=security.token_hash(token), created_by=admin.id,
                      expires_at=utcnow() + dt.timedelta(days=INVITATION_DAYS)))
    audit(db, request, admin.id, "invitacion_creada", email)
    # el enlace solo se muestra una vez: en la base de datos se guarda su hash
    base = request.app.state.settings.base_url or str(request.base_url).rstrip("/")
    request.session["new_invitation"] = {"email": email, "url": f"{base}/invitacion/{token}"}
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/invitaciones/{inv_id}/revocar", dependencies=[Depends(verify_csrf)])
def revoke(inv_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    inv = db.get(Invitation, inv_id)
    if inv is not None and inv.used_at is None:
        db.delete(inv)
        audit(db, request, admin.id, "invitacion_revocada", inv.email)
        flash(request, "Invitación revocada.")
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{user_id}/estado", dependencies=[Depends(verify_csrf)])
def toggle_active(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = other_user(db, admin, user_id)
    target.is_active = not target.is_active
    target.session_version = new_session_version()
    audit(db, request, admin.id, "usuario_activado" if target.is_active else "usuario_desactivado", target.email)
    flash(request, f"Cuenta de {target.email} {'activada' if target.is_active else 'desactivada'}.")
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{user_id}/rol", dependencies=[Depends(verify_csrf)])
def toggle_role(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = other_user(db, admin, user_id)
    target.role = "user" if target.is_admin else "admin"
    audit(db, request, admin.id, "rol_cambiado", f"{target.email} → {target.role}")
    flash(request, f"{target.email} ahora es {'administrador' if target.is_admin else 'usuario'}.")
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{user_id}/eliminar", dependencies=[Depends(verify_csrf)])
def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    target = other_user(db, admin, user_id)
    email = target.email
    erase_user(db, settings, target)
    audit(db, request, admin.id, "usuario_eliminado", "por el administrador")
    flash(request, f"Cuenta de {email} eliminada con todos sus datos y documentos.")
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.get("/registro")
def activity(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(200)).all()
    emails = dict(db.execute(select(User.id, User.email)).all())
    return render(request, "admin_log.html", user=admin, entries=entries, emails=emails)
