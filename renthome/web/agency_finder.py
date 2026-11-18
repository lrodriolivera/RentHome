"""Dónde encontrar webs de inmobiliarias reales de una ciudad o zona.

- OpenStreetMap: negocios registrados como inmobiliaria (`office`/`shop` = `estate_agent`) con su web.
- Brave Search (API oficial, con clave): resultados web de búsquedas como «inmobiliaria X alquiler anual».

Las sugerencias del modelo de IA se descartaron como fuente: en la prueba real inventaba dominios.
Lo encontrado aquí solo son candidatas; cada web se analiza y prueba antes de proponerla.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

from ..netguard import normalise_site
from . import catalog
from .area_sources import AREA_BOUNDS, AreaSources, SourceError
from .discovery import is_portal, site_key

log = logging.getLogger("renthome.discovery")

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_RESULTS_PER_QUERY = 20
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
    def __init__(self, sources: AreaSources | None = None, brave: BraveSearch | None = None) -> None:
        self.sources = sources or AreaSources()
        self.brave = brave

    @classmethod
    def from_env(cls) -> "AgencyFinder":
        return cls(AreaSources(), BraveSearch.from_env())

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
        if self.brave is None:
            notes.append("búsqueda web desactivada (sin clave de Brave Search)")
            return []
        urls: list[str] = []
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
