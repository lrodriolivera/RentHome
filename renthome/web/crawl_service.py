"""Rastreo compartido de inmobiliarias hacia la base de datos.

Incremental: en cada pasada se leen las páginas de listado, pero solo se abre la ficha de
los anuncios nuevos o de los que hace días que no se revisan. Así un "Buscar ahora" son
unas pocas peticiones por inmobiliaria en vez de cientos.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..crawl import discover
from ..filters import effective_price, evaluate
from ..parse import extract_with_text
from . import features, geo
from .models import Agency, Listing, utcnow
from .rules import BASE_CRITERIA

log = logging.getLogger("renthome.crawl")
DETAIL_REFRESH_DAYS = 3
MAX_DETAILS_PER_RUN = 120
MAX_URLS_PER_AGENCY = 400
TEXT_LIMIT = 6000


@dataclass
class CrawlResult:
    found: int = 0
    new: int = 0
    error: str = ""
    new_listing_ids: list[int] = field(default_factory=list)


def source_config(agency: Agency) -> dict:
    """Formato que entiende renthome.crawl.discover."""
    config = {
        "name": agency.name,
        "start_urls": [u.strip() for u in agency.start_urls.splitlines() if u.strip()],
        "detail_pattern": agency.detail_pattern,
        "max_pages": agency.max_pages,
        "render": agency.render,
        "render_details": agency.render_details,
        "long_term_section": agency.long_term_section,
    }
    if agency.page_pattern:
        config["page_pattern"] = agency.page_pattern
    return config


def build_fields(html: str, url: str, agency_name: str, areas: list[str], long_term_section: bool,
                 badge: str | None) -> dict:
    """Extrae y clasifica una ficha. Devuelve los valores de las columnas de Listing."""
    item, text = extract_with_text(html, url, agency_name)
    data = item.to_dict()
    if badge:
        data["status"] = badge
    eligible, notes, _score = evaluate(data, BASE_CRITERIA, long_term_section)
    slug = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    area, place = geo.detect([data["title"], slug, text[:1500]], areas)
    found = features.detect(data["title"], text)
    return {
        "title": data["title"][:200], "price": effective_price(data), "bedrooms": data["bedrooms"],
        "size_m2": found.size_m2, "property_type": found.property_type, "area": area, "place": place,
        "term": data["term"], "status": data["status"], "eligible": eligible,
        "notes": "; ".join(notes)[:300], "furnished": found.furnished, "pets": found.pets,
        "has_parking": found.has_parking, "has_elevator": found.has_elevator, "has_terrace": found.has_terrace,
        "image": (data.get("image") or "")[:500], "snippet": data["snippet"][:600], "text": text[:TEXT_LIMIT],
    }


def crawl_agency(factory: sessionmaker[Session], agency_id: int, fetcher) -> CrawlResult:
    """Rastrea una inmobiliaria. La red se usa fuera de la transacción para no bloquear la web."""
    with factory() as db:
        agency = db.get(Agency, agency_id)
        if agency is None:
            return CrawlResult(error="inmobiliaria eliminada")
        config, name, areas = source_config(agency), agency.name, agency.area_list
        long_term = agency.long_term_section
        known = {l.url: l.detail_checked_at for l in db.scalars(select(Listing).where(Listing.agency_id == agency_id))}

    now = utcnow()
    try:
        urls, badges = discover(fetcher, config)
    except Exception as exc:  # red, regex inválido de una candidata, etc.
        log.warning("%s: fallo al leer los listados: %s", name, exc)
        return _finish(factory, agency_id, CrawlResult(error=f"no se pudieron leer los listados: {exc}"[:200]))
    if not urls:
        return _finish(factory, agency_id, CrawlResult(error="no se encontraron fichas (¿cambió la web?)"))
    urls = urls[:MAX_URLS_PER_AGENCY]

    fresh: dict[str, dict] = {}
    budget = MAX_DETAILS_PER_RUN
    for url in urls:
        checked = known.get(url)
        stale = url not in known or checked is None or now - checked > dt.timedelta(days=DETAIL_REFRESH_DAYS)
        if not stale or budget <= 0:
            continue
        budget -= 1
        html = fetcher.get(url, render=config["render_details"])
        if not html:
            continue
        try:
            fresh[url] = build_fields(html, url, name, areas, long_term, badges.get(url))
        except Exception as exc:  # una ficha rara no detiene el resto
            log.warning("%s: error extrayendo %s: %s", name, url, exc)

    result = CrawlResult(found=len(urls))
    with factory() as db:
        existing = {l.url: l for l in db.scalars(select(Listing).where(Listing.agency_id == agency_id))}
        seen = set(urls)
        for url in urls:
            listing, values = existing.get(url), fresh.get(url)
            if listing is None:
                if values is None:
                    continue  # no se pudo leer la ficha: se reintenta en la próxima pasada
                listing = Listing(agency_id=agency_id, url=url, first_seen=now)
                db.add(listing)
                result.new += 1 if values["eligible"] else 0
            if values is not None:
                if listing.price and values["price"] and values["price"] < listing.price:
                    listing.price_prev = listing.price
                for column, value in values.items():
                    setattr(listing, column, value)
                listing.detail_checked_at = now
            elif url in badges and listing.status != badges[url]:
                # el listado dice que ya está alquilado/reservado
                listing.status, listing.eligible = badges[url], False
                listing.notes = "alquilado/reservado"
            listing.active, listing.last_seen = True, now
        for url, listing in existing.items():
            if url not in seen:
                listing.active = False
        db.flush()
        result.new_listing_ids = [l.id for l in db.scalars(
            select(Listing).where(Listing.agency_id == agency_id, Listing.first_seen == now, Listing.eligible))]
        db.commit()
    return _finish(factory, agency_id, result)


def _finish(factory: sessionmaker[Session], agency_id: int, result: CrawlResult) -> CrawlResult:
    with factory() as db:
        agency = db.get(Agency, agency_id)
        if agency is not None:
            agency.last_crawled_at = utcnow()
            agency.last_status = result.error
            if result.error:
                agency.consecutive_failures += 1
            else:
                agency.consecutive_failures, agency.last_ok_at, agency.last_found = 0, utcnow(), result.found
            db.commit()
    return result
