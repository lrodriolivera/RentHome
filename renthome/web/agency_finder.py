"""Dónde encontrar webs de inmobiliarias reales de una ciudad o zona.

- OpenStreetMap: negocios registrados como inmobiliaria (`office`/`shop` = `estate_agent`) con su web.
- Google Places (Text Search, con clave): negocios de Google Maps; solo se pide su web (`websiteUri`).
  Un contador mensual en la base de datos impide pasar del cupo gratuito (1.000 consultas al mes).
- Brave Search (API oficial, con clave): resultados web de búsquedas como «inmobiliaria X alquiler anual».

Las sugerencias del modelo de IA se descartaron como fuente: en la prueba real inventaba dominios.
Lo encontrado aquí solo son candidatas; cada web se analiza y prueba antes de proponerla.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from ..netguard import normalise_site
from . import catalog
from .area_sources import AREA_BOUNDS, AreaSources, SourceError
from .discovery import is_portal, site_key
from .models import AppState

log = logging.getLogger("renthome.discovery")

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_RESULTS_PER_QUERY = 20
GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FREE_MONTHLY = 1000       # cupo gratuito del SKU «Text Search Enterprise» (incluye websiteUri)
GOOGLE_DEFAULT_MONTHLY = 900     # margen bajo el cupo por si la clave se usa en otra parte
GOOGLE_RESULTS_PER_QUERY = 20
TOWN_RADIUS_M = 6000
WEBSITE_TAGS = ("website", "contact:website", "url")


@dataclass(frozen=True)
class FoundSites:
    sites: list[str]      # «https://dominio», sin repetir, primero las de OpenStreetMap
    osm: int              # cuántas vinieron de OpenStreetMap
    web: int              # cuántas nuevas aportó la búsqueda web
    notes: str = ""       # avisos legibles (fuente sin configurar o que falló)


class BraveSearch:
    """API web de Brave Search. https://api.search.brave.com (clave en RENTHOME_BRAVE_API_KEY)."""

    def __init__(self, key: str, http=None) -> None:
        self.key, self.http = key, http or requests.Session()

    @classmethod
    def from_env(cls) -> "BraveSearch | None":
        key = os.environ.get("RENTHOME_BRAVE_API_KEY", "").strip()
        return cls(key) if key else None

    def search(self, query: str) -> list[str]:
        try:
            response = self.http.get(BRAVE_URL, timeout=20, params={
                "q": query, "country": "ES", "search_lang": "es", "count": BRAVE_RESULTS_PER_QUERY,
                "safesearch": "moderate"}, headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
        except requests.RequestException as exc:
            raise SourceError(f"Brave: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise SourceError(f"Brave: HTTP {response.status_code}")
        try:
            results = (response.json().get("web") or {}).get("results") or []
        except ValueError as exc:
            raise SourceError("Brave: respuesta no válida") from exc
        return [str(item.get("url") or "") for item in results if isinstance(item, dict)]


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class MonthlyBudget:
    """Cuántas consultas de pago se han hecho este mes; se niega a pasar de `limit`."""

    def __init__(self, factory, name: str, limit: int, month: Callable[[], str] = _this_month) -> None:
        self.factory, self.name, self.limit, self.month = factory, name, limit, month
        self._lock = threading.Lock()

    def _key(self) -> str:
        return f"{self.name}:{self.month()}"

    def used(self) -> int:
        with self.factory() as db:
            state = db.get(AppState, self._key())
            return int(state.value) if state and state.value.isdigit() else 0

    def take(self) -> bool:
        with self._lock, self.factory() as db:
            key = self._key()
            state = db.get(AppState, key)
            used = int(state.value) if state and state.value.isdigit() else 0
            if used >= self.limit:
                return False
            if state is None:
                state = AppState(key=key)
                db.add(state)
            state.value = str(used + 1)
            db.commit()
            return True


class GooglePlaces:
    """Text Search (New) de Google Places. Clave en RENTHOME_GOOGLE_PLACES_KEY.

    Solo se pide `websiteUri`: no se guarda nada de Google, la web se analiza después por su cuenta.
    """

    def __init__(self, key: str, budget, http=None) -> None:
        self.key, self.budget, self.http = key, budget, http or requests.Session()

    @classmethod
    def from_env(cls, factory) -> "GooglePlaces | None":
        key = os.environ.get("RENTHOME_GOOGLE_PLACES_KEY", "").strip()
        if not key:
            return None
        try:
            limit = int(os.environ.get("RENTHOME_GOOGLE_PLACES_MONTHLY", "") or GOOGLE_DEFAULT_MONTHLY)
        except ValueError:
            limit = GOOGLE_DEFAULT_MONTHLY
        limit = max(0, min(limit, GOOGLE_FREE_MONTHLY))
        return cls(key, MonthlyBudget(factory, "gplaces", limit))

    def search(self, query: str, area: str) -> list[str]:
        if not self.budget.take():
            raise SourceError("Google Places: cupo gratuito del mes agotado")
        west, south, east, north = AREA_BOUNDS[area]
        body = {"textQuery": query, "languageCode": "es", "regionCode": "ES", "pageSize": GOOGLE_RESULTS_PER_QUERY,
                "locationRestriction": {"rectangle": {"low": {"latitude": south, "longitude": west},
                                                      "high": {"latitude": north, "longitude": east}}}}
        try:
            response = self.http.post(GOOGLE_URL, json=body, timeout=20, headers={
                "X-Goog-Api-Key": self.key, "X-Goog-FieldMask": "places.websiteUri"})
        except requests.RequestException as exc:
            raise SourceError(f"Google Places: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise SourceError(f"Google Places: HTTP {response.status_code}")
        try:
            places = response.json().get("places") or []
        except ValueError as exc:
            raise SourceError("Google Places: respuesta no válida") from exc
        return [str(p["websiteUri"]) for p in places if isinstance(p, dict) and p.get("websiteUri")]


def _google_query(area: str, town: str) -> str:
    region = catalog.AREAS.get(area, area)
    return f"inmobiliaria en {town}, {region}" if town else f"inmobiliaria alquiler {region}"


def _queries(area: str, town: str) -> list[str]:
    region = catalog.AREAS.get(area, area)
    place = town or region
    return [f"inmobiliaria {place} alquiler anual", f"inmobiliaria {place} {region} alquiler larga duración"]


def _osm_query(area: str, center: tuple[float, float] | None) -> str:
    if center is not None:
        scope = f"(around:{TOWN_RADIUS_M},{center[0]:.5f},{center[1]:.5f})"
    else:
        west, south, east, north = AREA_BOUNDS[area]
        scope = f"({south},{west},{north},{east})"
    return f'[out:json][timeout:60];(nwr["office"="estate_agent"]{scope};nwr["shop"="estate_agent"]{scope};);out tags 300;'


class AgencyFinder:
    def __init__(self, sources: AreaSources | None = None, brave: BraveSearch | None = None,
                 google: GooglePlaces | None = None) -> None:
        self.sources = sources or AreaSources()
        self.brave, self.google = brave, google

    @classmethod
    def from_env(cls, factory=None) -> "AgencyFinder":
        return cls(AreaSources(), BraveSearch.from_env(), GooglePlaces.from_env(factory))

    @property
    def web_search_ready(self) -> bool:
        return self.google is not None or self.brave is not None

    @property
    def web_sources(self) -> str:
        """Nombre legible de las fuentes web activas («Google Maps y Brave Search»)."""
        names = [n for n, s in (("Google Maps", self.google), ("Brave Search", self.brave)) if s is not None]
        return " y ".join(names)

    def _osm(self, area: str, town: str, notes: list[str]) -> list[str]:
        center = None
        if town:
            location = self.sources.geocode(town, area)
            if location is None:
                notes.append(f"{town} no se encontró en OpenStreetMap")
                return []
            center = (location.lat, location.lon)
        try:
            payload = self.sources.overpass(_osm_query(area, center))
        except SourceError:
            notes.append("OpenStreetMap no respondió")
            return []
        return [next((t[k] for k in WEBSITE_TAGS if t.get(k)), "")
                for t in ((e.get("tags") or {}) for e in payload.get("elements") or [])]

    def _web(self, area: str, town: str, notes: list[str]) -> list[str]:
        if not self.web_search_ready:
            notes.append("búsqueda web desactivada (sin clave de Google Places ni de Brave Search)")
            return []
        urls: list[str] = []
        if self.google is not None:
            try:
                urls += self.google.search(_google_query(area, town), area)
            except SourceError as exc:
                log.warning("Google Places: %s", exc)
                notes.append("cupo gratuito de Google Maps agotado este mes" if "cupo" in str(exc)
                             else "Google Maps no respondió")
        if self.brave is None:
            return urls
        for query in _queries(area, town):
            try:
                urls += self.brave.search(query)
            except SourceError as exc:
                log.warning("Búsqueda web fallida: %s", exc)
                notes.append("la búsqueda web no respondió")
                break
        return urls

    def find(self, area: str, town: str) -> FoundSites:
        """Webs de inmobiliarias de una ciudad (o de toda la zona si `town` está vacío)."""
        notes: list[str] = []
        sites: dict[str, str] = {}
        counts = {"osm": 0, "web": 0}
        for origin, raw_urls in (("osm", self._osm(area, town, notes)), ("web", self._web(area, town, notes))):
            for raw in raw_urls:
                site = normalise_site(raw[:200]) if raw else None
                if site is None or is_portal(site) or site_key(site) in sites:
                    continue
                sites[site_key(site)] = site
                counts[origin] += 1
        return FoundSites(sites=list(sites.values()), osm=counts["osm"], web=counts["web"], notes="; ".join(notes))
