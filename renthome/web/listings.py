"""Anuncios ya rastreados (data/listings.json) filtrados según las preferencias.

Solución temporal de la fase 1: la búsqueda por usuario llegará con la fase 2.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .models import SearchPreference

MIN_PLAUSIBLE_RENT = 300


@dataclass(frozen=True)
class ListingView:
    title: str
    url: str
    source: str
    price: int | None
    bedrooms: int | None
    location: str
    image: str | None
    first_seen: str
    uncertain_term: bool
    preferred: bool


def _price(item: dict) -> int | None:
    if item.get("annual_price") and item.get("term") in ("long", "mixed"):
        return item["annual_price"]
    return item.get("price")


def _safe_url(value: object, schemes: tuple[str, ...]) -> str | None:
    """Los anuncios vienen de webs externas: se descarta cualquier enlace que no sea web."""
    if not isinstance(value, str):
        return None
    return value if urlparse(value).scheme in schemes else None


def load_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("listings", {}).values())
    except (json.JSONDecodeError, OSError):
        return []


def matching(path: Path, pref: SearchPreference | None) -> list[ListingView]:
    """Alquileres de larga duración activos que caben en el presupuesto del usuario."""
    max_price = pref.max_price if pref and pref.max_price else None
    min_beds = pref.min_bedrooms if pref else 1
    preferred = pref.preferred_bedrooms if pref else None
    out: list[ListingView] = []
    for item in load_raw(path):
        # 'matches' ya garantiza: alquiler anual, disponible, vivienda y dentro de Ibiza
        if not item.get("active") or not item.get("matches"):
            continue
        url = _safe_url(item.get("url"), ("http", "https"))
        if url is None:
            continue
        price, beds = _price(item), item.get("bedrooms")
        if price is not None and (price < MIN_PLAUSIBLE_RENT or (max_price and price > max_price)):
            continue
        if beds is not None and beds < min_beds:
            continue
        out.append(ListingView(
            title=item.get("title") or url, url=url, source=item.get("source", ""),
            price=price, bedrooms=beds, location=item.get("location") or "Ibiza",
            image=_safe_url(item.get("image"), ("https",)),
            first_seen=item.get("first_matched") or item.get("first_seen") or "",
            uncertain_term=item.get("term") != "long", preferred=preferred is not None and beds == preferred,
        ))
    return sorted(out, key=lambda v: (not v.preferred, v.price or 10**9))
