"""Anuncios de la base compartida que encajan con los criterios de un usuario."""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, defer, joinedload

from ..parse import norm
from . import catalog
from .criteria import area_keys, wanted_services
from .models import AreaReport, Listing, UserListing, utcnow

MIN_PLAUSIBLE_RENT = 300
NEW_WINDOW_DAYS = 7
SERVICE_POINTS = 10


@dataclass(frozen=True)
class Match:
    listing: Listing
    score: int
    is_new: bool
    is_preferred: bool
    state: str            # "" | saved
    highlights: tuple[str, ...]
    fit_score: int | None = None
    fit_reason: str = ""
    services_unknown: bool = False   # se pidieron servicios cerca y la zona aún no está analizada


def _zone_terms(zones: str) -> list[str]:
    return [t for t in (norm(z) for z in zones.replace(";", ",").split(",")) if len(t) >= 3]


def _score(listing: Listing, criteria, zone_terms: list[str]) -> tuple[int, bool, tuple[str, ...]]:
    score, highlights = 0, []
    preferred = criteria.preferred_bedrooms is not None and listing.bedrooms == criteria.preferred_bedrooms
    if preferred:
        score += 100
    haystack = norm(f"{listing.title} {listing.place} {listing.snippet}")
    if any(term in haystack for term in zone_terms):
        score += 40
        highlights.append("en tu zona")
    wanted = (
        (criteria.needs_parking, listing.has_parking, "parking"),
        (criteria.needs_elevator, listing.has_elevator, "ascensor"),
        (criteria.wants_terrace, listing.has_terrace, "terraza"),
        (criteria.needs_pets_allowed, listing.pets == "yes", "admite mascotas"),
    )
    for wants, has, label in wanted:
        if wants and has:
            score += 15
            highlights.append(label)
    if listing.term == "long":
        score += 5
    if listing.price is None:
        score -= 40
    return score, preferred, tuple(highlights)


def _passes(listing: Listing, criteria, types: set[str]) -> bool:
    if listing.price is not None and listing.price < MIN_PLAUSIBLE_RENT:
        return False
    if listing.bedrooms is not None and listing.bedrooms < criteria.min_bedrooms:
        return False
    if types and listing.property_type and listing.property_type not in types:
        return False
    if criteria.min_size_m2 and listing.size_m2 and listing.size_m2 < criteria.min_size_m2:
        return False
    # solo se descarta por muebles o mascotas cuando el anuncio lo dice expresamente
    if criteria.furnished in ("yes", "no") and listing.furnished and listing.furnished != criteria.furnished:
        return False
    if criteria.needs_pets_allowed and listing.pets == "no":
        return False
    return True


def zone_services(db: Session) -> dict[int, set[str] | None]:
    """Servicios con algo cerca de cada piso analizado. None: analizado, pero sin datos de servicios."""
    rows = db.execute(select(AreaReport.listing_id, AreaReport.services).where(AreaReport.status == "done"))
    return {lid: (set(s.split(",")) - {""} if s is not None else None) for lid, s in rows}


def _services(wanted: list[str], have: set[str] | None, required: bool) -> tuple[bool, int, tuple[str, ...], bool]:
    """(pasa, puntos, etiquetas, sin datos) para los servicios deseados."""
    if not wanted:
        return True, 0, (), False
    if have is None:
        return True, 0, (), True
    present = [s for s in wanted if s in have]
    if required and len(present) < len(wanted):
        return False, 0, (), False
    return True, SERVICE_POINTS * len(present), tuple(f"{catalog.SERVICE_SHORT[s]} cerca" for s in present), False


def find(db: Session, criteria, user_id: int, new_since: dt.datetime | None = None,
         only_saved: bool = False, read_reason: Callable[[UserListing], str] | None = None) -> list[Match]:
    """Anuncios activos de alquiler de larga duración que cumplen los criterios, mejores primero.

    `read_reason` descifra el motivo del encaje (dato derivado del perfil); sin él no se muestra.
    """
    areas = area_keys(criteria) if criteria is not None else []
    if not areas and not only_saved:
        return []
    states = {s.listing_id: s for s in db.scalars(select(UserListing).where(UserListing.user_id == user_id))}
    # el texto completo de la ficha solo lo usa la IA: no se carga para pintar listados
    base = select(Listing).options(joinedload(Listing.agency), defer(Listing.text))
    query = base.where(Listing.active, Listing.eligible)
    if only_saved:
        saved_ids = [lid for lid, s in states.items() if s.state == "saved"]
        query = base.where(Listing.id.in_(saved_ids))
    else:
        query = query.where(Listing.area.in_(areas))
        if criteria.max_price:
            query = query.where(or_(Listing.price.is_(None), Listing.price <= criteria.max_price))
    types = {t for t in (getattr(criteria, "property_types", "") or "").split(",") if t}
    zone_terms = _zone_terms(getattr(criteria, "zones", "") or "")
    since = new_since or utcnow() - dt.timedelta(days=NEW_WINDOW_DAYS)
    wanted = wanted_services(criteria) if criteria is not None else []
    required = bool(getattr(criteria, "services_required", False)) and not only_saved
    zones = zone_services(db) if wanted else {}

    matches = []
    for listing in db.scalars(query):
        state = states.get(listing.id)
        if not only_saved and ((state and state.state == "hidden") or not _passes(listing, criteria, types)):
            continue
        score, preferred, highlights = _score(listing, criteria, zone_terms) if criteria is not None else (0, False, ())
        passes, points, near, unknown = _services(wanted, zones.get(listing.id), required)
        if not passes:
            continue
        score, highlights = score + points, highlights + near
        matches.append(Match(
            listing=listing, score=score, is_new=listing.first_seen > since, is_preferred=preferred,
            state=state.state if state else "", highlights=highlights,
            fit_score=state.fit_score if state else None,
            fit_reason=read_reason(state) if state and read_reason else "",
            services_unknown=unknown,
        ))
    return sorted(matches, key=lambda m: (-(m.fit_score or 0) if only_saved else 0, -m.score,
                                          m.listing.price or 10**9, m.listing.id))


def count_new(db: Session, criteria, user_id: int, since: dt.datetime | None) -> int:
    return sum(1 for m in find(db, criteria, user_id, new_since=since) if m.is_new)
