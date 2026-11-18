"""Página «Explorar la zona» de un piso: informe con datos públicos y resumen de la IA."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import area_report
from .area_sources import CATEGORIES
from .deps import audit, flash, get_db, render, require_user, verify_csrf
from .models import AreaReport, AuditLog, Listing, User, utcnow

router = APIRouter()
AUDIT_ACTION = "zona_informe"
PENDING = ("queued", "running")


def _listing(db: Session, listing_id: int) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404)
    return listing


def _report(db: Session, listing_id: int) -> AreaReport | None:
    return db.scalar(select(AreaReport).where(AreaReport.listing_id == listing_id))


def _requests_today(db: Session, user: User) -> int:
    since = utcnow() - dt.timedelta(days=1)
    return db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.user_id == user.id, AuditLog.action == AUDIT_ACTION, AuditLog.at >= since)) or 0


@router.get("/pisos/{listing_id}/zona")
def area_page(listing_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    listing = _listing(db, listing_id)
    report = _report(db, listing_id)
    facts, story = area_report.decode(report)
    return render(request, "area.html", user=user, listing=listing, report=report, facts=facts, story=story,
                  pending=report is not None and report.status in PENDING,
                  can_request=report is None or area_report.can_refresh(report), categories=CATEGORIES)


@router.post("/pisos/{listing_id}/zona", dependencies=[Depends(verify_csrf)])
def request_area(listing_id: int, request: Request, user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    _listing(db, listing_id)
    back = RedirectResponse(f"/pisos/{listing_id}/zona", status_code=303)
    report = _report(db, listing_id)
    if report is not None and not area_report.can_refresh(report):
        return back  # ya está en marcha o es reciente: no se repite
    if _requests_today(db, user) >= area_report.MAX_REPORTS_PER_DAY:
        flash(request, f"Has llegado al límite de {area_report.MAX_REPORTS_PER_DAY} informes de zona por día. "
                       "Vuelve a intentarlo mañana.", "error")
        return back
    if report is None:
        report = AreaReport(listing_id=listing_id)
        db.add(report)
    report.status, report.error, report.requested_by, report.updated_at = "queued", "", user.id, utcnow()
    audit(db, request, user.id, AUDIT_ACTION, f"piso {listing_id}")
    db.commit()  # antes de encolar: el informe se prepara desde otro hilo
    request.app.state.area_runner.submit(report.id)
    return back
