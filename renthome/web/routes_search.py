"""Resultados de búsqueda, búsquedas guardadas y anuncios guardados/descartados."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import criteria as criteria_lib
from . import forms, matching
from .deps import flash, get_db, render, require_user, verify_csrf
from .routes_profile import ensure_preference
from .models import Agency, CrawlJob, Listing, SavedSearch, User, UserListing, utcnow
from .worker import USER_REFRESH_HOURS, stale_agencies

router = APIRouter()
MAX_SAVED_SEARCHES = 12
LISTING_STATES = {"saved": "saved", "hidden": "hidden", "none": ""}


def own_search(db: Session, user: User, search_id: int) -> SavedSearch:
    search = db.get(SavedSearch, search_id)
    if search is None or search.user_id != user.id:
        raise HTTPException(status_code=404)
    return search


def safe_next(value: str, default: str) -> str:
    """Solo rutas internas: evita redirecciones a otros sitios."""
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else default


def crawl_status(db: Session, areas: list[str]) -> dict:
    """Estado del rastreo de las inmobiliarias que cubren esas zonas."""
    agencies = [a for a in db.scalars(select(Agency).where(Agency.enabled)) if set(a.area_list) & set(areas)]
    ids = [a.id for a in agencies]
    pending = db.scalar(select(func.count()).select_from(CrawlJob).where(
        CrawlJob.agency_id.in_(ids), CrawlJob.status.in_(("queued", "running")))) if ids else 0
    crawled = [a.last_crawled_at for a in agencies if a.last_crawled_at]
    return {
        "agencies": len(agencies), "pending": pending or 0,
        "oldest": min(crawled) if len(crawled) == len(agencies) and crawled else None,
        "never": len(agencies) - len(crawled),
        "can_refresh": bool(stale_agencies(db, areas, USER_REFRESH_HOURS, include_failing=True)),
    }


@router.get("/pisos")
def results(request: Request, b: int | None = None, user: User = Depends(require_user),
            db: Session = Depends(get_db)):
    saved = own_search(db, user, b) if b is not None else None
    criteria = saved or user.preference
    areas = criteria_lib.area_keys(criteria) if criteria else []
    since = saved.last_viewed_at if saved else None
    matches = matching.find(db, criteria, user.id, new_since=since) if areas else []
    if saved is not None:
        saved.last_viewed_at = utcnow()
    return render(request, "results.html", user=user, matches=matches, saved=saved, criteria=criteria,
                  summary=criteria_lib.summary(criteria) if areas else "", status=crawl_status(db, areas),
                  searches=user.saved_searches, back=f"/pisos?b={saved.id}" if saved else "/pisos")


@router.post("/pisos/actualizar", dependencies=[Depends(verify_csrf)])
async def refresh(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await request.form()
    search_id = forms.integer(form, "b", 1, 10**9) if form.get("b") else None
    criteria = own_search(db, user, search_id) if search_id else user.preference
    areas = criteria_lib.area_keys(criteria) if criteria else []
    back = f"/pisos?b={search_id}" if search_id else "/pisos"
    if not areas:
        flash(request, "Primero elige en qué zonas buscas.", "error")
        return RedirectResponse("/busqueda", status_code=303)
    queued = request.app.state.worker.enqueue_stale(db, areas, USER_REFRESH_HOURS, "user")
    if queued:
        flash(request, f"Buscando en {queued} inmobiliarias. Los pisos nuevos irán apareciendo aquí.")
    else:
        flash(request, "Ya está todo al día: estas inmobiliarias se han revisado hace menos de "
                       f"{USER_REFRESH_HOURS} horas.")
    return RedirectResponse(back, status_code=303)


@router.get("/api/rastreo")
def crawl_progress(areas: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    status = crawl_status(db, [a for a in areas.split(",") if a])
    return {"pending": status["pending"]}


@router.post("/pisos/{listing_id}/estado", dependencies=[Depends(verify_csrf)])
async def set_listing_state(listing_id: int, request: Request, user: User = Depends(require_user),
                            db: Session = Depends(get_db)):
    form = await request.form()
    state = LISTING_STATES.get(str(form.get("state") or ""))
    if state is None or db.get(Listing, listing_id) is None:
        raise HTTPException(status_code=404)
    current = db.get(UserListing, (user.id, listing_id))
    if state == "":
        if current is not None:
            db.delete(current)
    elif current is None:
        db.add(UserListing(user_id=user.id, listing_id=listing_id, state=state))
    else:
        current.state = state
    return RedirectResponse(safe_next(str(form.get("next") or ""), "/pisos"), status_code=303)


@router.get("/favoritos")
def favourites(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    matches = matching.find(db, user.preference, user.id, only_saved=True)
    hidden = db.scalar(select(func.count()).select_from(UserListing).where(
        UserListing.user_id == user.id, UserListing.state == "hidden"))
    return render(request, "favourites.html", user=user, matches=matches, hidden_count=hidden or 0, back="/favoritos")


@router.post("/favoritos/restaurar-descartados", dependencies=[Depends(verify_csrf)])
def restore_hidden(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    for state in db.scalars(select(UserListing).where(UserListing.user_id == user.id, UserListing.state == "hidden")):
        db.delete(state)
    flash(request, "Los anuncios descartados vuelven a aparecer en tus búsquedas.")
    return RedirectResponse("/favoritos", status_code=303)


# ---------- búsquedas guardadas ----------

@router.get("/busquedas")
def searches(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = [{"search": s, "summary": criteria_lib.summary(s),
             "total": len(found := matching.find(db, s, user.id, new_since=s.last_viewed_at or s.created_at)),
             "new": sum(1 for m in found if m.is_new)}
            for s in sorted(user.saved_searches, key=lambda s: s.created_at)]
    return render(request, "searches.html", user=user, rows=rows, max_searches=MAX_SAVED_SEARCHES)


@router.get("/busquedas/nueva")
def new_search(request: Request, user: User = Depends(require_user)):
    # parte de la búsqueda principal del perfil para no empezar de cero
    return render(request, "search_form.html", user=user, search=None, values=ensure_preference(user), name="")


@router.get("/busquedas/{search_id}/editar")
def edit_search(search_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    search = own_search(db, user, search_id)
    return render(request, "search_form.html", user=user, search=search, values=search, name=search.name)


async def _save_search(request: Request, user: User, db: Session, search: SavedSearch | None):
    form = await request.form()
    try:
        name = forms.text(form, "name", 80, required=True)
        values = criteria_lib.read_form(form)
    except forms.FormError as exc:
        return render(request, "search_form.html", user=user, search=search, status_code=400, error=str(exc),
                      values=search or ensure_preference(user), name=str(form.get("name") or "")[:80])
    if search is None:
        if len(user.saved_searches) >= MAX_SAVED_SEARCHES:
            flash(request, f"Puedes guardar hasta {MAX_SAVED_SEARCHES} búsquedas. Borra alguna primero.", "error")
            return RedirectResponse("/busquedas", status_code=303)
        search = SavedSearch(user_id=user.id, last_viewed_at=utcnow())
        db.add(search)
    search.name, search.notify = name, forms.flag(form, "notify")
    for field, value in values.items():
        setattr(search, field, value)
    db.flush()
    flash(request, f"Búsqueda «{name}» guardada.")
    return RedirectResponse(f"/pisos?b={search.id}", status_code=303)


@router.post("/busquedas", dependencies=[Depends(verify_csrf)])
async def create_search(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return await _save_search(request, user, db, None)


@router.post("/busquedas/{search_id}/editar", dependencies=[Depends(verify_csrf)])
async def update_search(search_id: int, request: Request, user: User = Depends(require_user),
                        db: Session = Depends(get_db)):
    return await _save_search(request, user, db, own_search(db, user, search_id))


@router.post("/busquedas/{search_id}/borrar", dependencies=[Depends(verify_csrf)])
def delete_search(search_id: int, request: Request, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    search = own_search(db, user, search_id)
    db.delete(search)
    flash(request, f"Búsqueda «{search.name}» borrada.")
    return RedirectResponse("/busquedas", status_code=303)
