"""Panel de administrador: inmobiliarias que se rastrean."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import agencies as agency_lib
from . import catalog, forms
from .deps import audit, flash, get_db, render, require_admin, verify_csrf
from .models import Agency, CrawlJob, Listing, User

router = APIRouter(prefix="/admin/inmobiliarias")


def get_agency(db: Session, agency_id: int) -> Agency:
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404)
    return agency


FORM_FIELDS = ("name", "website", "town", "areas", "start_urls", "detail_pattern", "page_pattern", "max_pages",
               "render", "render_details", "long_term_section", "notes")


def form_values(agency: Agency | None, form=None) -> dict:
    """Valores con los que pintar el formulario: lo recién enviado, o lo guardado."""
    if form is not None:
        values = {field: str(form.get(field) or "") for field in FORM_FIELDS}
        values["areas"] = form.getlist("areas")
        for flag in ("render", "render_details", "long_term_section"):
            values[flag] = values[flag] == "1"
        return values
    if agency is None:
        return {field: "" for field in FORM_FIELDS} | {"areas": [], "max_pages": 3}
    return {field: getattr(agency, field) for field in FORM_FIELDS} | {"areas": agency.area_list}


def read_agency_form(form) -> dict:
    return {
        "name": forms.text(form, "name", 120, required=True),
        "website": forms.text(form, "website", 200),
        "town": forms.text(form, "town", 80),
        "areas": forms.choices(form, "areas", catalog.AREAS).split(","),
        "start_urls": [u for u in str(form.get("start_urls") or "").split() if u],
        "detail_pattern": str(form.get("detail_pattern") or "").strip(),
        "page_pattern": str(form.get("page_pattern") or "").strip(),
        "max_pages": forms.integer(form, "max_pages", 1, 20, default=3),
        "render": forms.flag(form, "render"),
        "render_details": forms.flag(form, "render_details"),
        "long_term_section": forms.flag(form, "long_term_section"),
    }


@router.get("")
def agencies(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Agency).order_by(Agency.areas, Agency.name)).all()
    counts = dict(db.execute(select(Listing.agency_id, func.count()).where(Listing.active, Listing.eligible)
                             .group_by(Listing.agency_id)).all())
    busy = set(db.scalars(select(CrawlJob.agency_id).where(CrawlJob.status.in_(("queued", "running")))))
    jobs = db.scalars(select(CrawlJob).order_by(CrawlJob.id.desc()).limit(15)).all()
    names = {a.id: a.name for a in rows}
    return render(request, "admin_agencies.html", user=admin, agencies=rows, counts=counts, busy=busy, jobs=jobs,
                  names=names)


@router.get("/nueva")
def new_agency(request: Request, admin: User = Depends(require_admin)):
    return render(request, "admin_agency_form.html", user=admin, agency=None, values=form_values(None))


@router.get("/{agency_id}")
def edit_agency(agency_id: int, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    agency = get_agency(db, agency_id)
    return render(request, "admin_agency_form.html", user=admin, agency=agency, values=form_values(agency))


async def _save(request: Request, admin: User, db: Session, agency: Agency | None):
    form = await request.form()
    target = agency or Agency(origin="admin")
    try:
        entry = read_agency_form(form)
        clash = db.scalar(select(Agency).where(Agency.name == entry["name"], Agency.id != (target.id or 0)))
        if clash is not None:
            raise forms.FormError("Ya hay una inmobiliaria con ese nombre.")
        agency_lib.apply_entry(target, entry)
    except (forms.FormError, agency_lib.AgencyConfigError) as exc:
        db.rollback()
        return render(request, "admin_agency_form.html", user=admin, agency=agency, status_code=400, error=str(exc),
                      values=form_values(agency, form))
    target.notes = forms.text(form, "notes", 300)
    target.consecutive_failures = 0
    db.add(target)
    db.flush()
    audit(db, request, admin.id, "inmobiliaria_guardada", target.name)
    flash(request, f"{target.name} guardada. Pulsa «Rastrear» para probarla.")
    return RedirectResponse("/admin/inmobiliarias", status_code=303)


@router.post("", dependencies=[Depends(verify_csrf)])
async def create_agency(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return await _save(request, admin, db, None)


@router.post("/rastrear-todas", dependencies=[Depends(verify_csrf)])
def crawl_all(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ids = list(db.scalars(select(Agency.id).where(Agency.enabled)))
    queued = request.app.state.worker.enqueue(db, ids, "admin")
    flash(request, f"{queued} inmobiliarias en cola.")
    return RedirectResponse("/admin/inmobiliarias", status_code=303)


@router.post("/{agency_id}", dependencies=[Depends(verify_csrf)])
async def update_agency(agency_id: int, request: Request, admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    return await _save(request, admin, db, get_agency(db, agency_id))


@router.post("/{agency_id}/rastrear", dependencies=[Depends(verify_csrf)])
def crawl_one(agency_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    agency = get_agency(db, agency_id)
    queued = request.app.state.worker.enqueue(db, [agency.id], "admin")
    flash(request, f"{agency.name}: " + ("rastreo en cola." if queued else "ya tenía un rastreo pendiente."))
    return RedirectResponse("/admin/inmobiliarias", status_code=303)


@router.post("/{agency_id}/estado", dependencies=[Depends(verify_csrf)])
def toggle_agency(agency_id: int, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    agency = get_agency(db, agency_id)
    agency.enabled = not agency.enabled
    agency.consecutive_failures = 0
    audit(db, request, admin.id, "inmobiliaria_activada" if agency.enabled else "inmobiliaria_pausada", agency.name)
    flash(request, f"{agency.name} {'activada' if agency.enabled else 'en pausa'}.")
    return RedirectResponse("/admin/inmobiliarias", status_code=303)


@router.post("/{agency_id}/eliminar", dependencies=[Depends(verify_csrf)])
def delete_agency(agency_id: int, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    agency = get_agency(db, agency_id)
    name = agency.name
    db.delete(agency)
    audit(db, request, admin.id, "inmobiliaria_eliminada", name)
    flash(request, f"{name} eliminada con sus anuncios.")
    return RedirectResponse("/admin/inmobiliarias", status_code=303)
