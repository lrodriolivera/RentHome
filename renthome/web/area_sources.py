"""Datos públicos de la zona de un piso: OpenStreetMap (Nominatim y Overpass), Wikipedia y Commons.

Solo se envía el nombre del lugar o sus coordenadas, nunca datos de usuarios. Todas las fuentes
permiten consultas automáticas con un User-Agent que identifique la aplicación y un uso moderado
(los informes se guardan y se reutilizan).
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from ..parse import norm

log = logging.getLogger("renthome.area")

USER_AGENT = "RentHome/1.0 (buscador personal de alquiler de larga duración; uso no comercial)"
TIMEOUT_SECONDS = 25
OVERPASS_TIMEOUT_SECONDS = 70
OVERPASS_ROUNDS = 2                  # los servidores públicos se saturan a ratos: una segunda vuelta
OVERPASS_RETRY_PAUSE_SECONDS = 20
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
WIKIPEDIA_API = "https://es.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
SAFE_IMAGE_PREFIXES = ("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/")
SAFE_PAGE_PREFIXES = ("https://commons.wikimedia.org/", "https://es.wikipedia.org/")

# (oeste, sur, este, norte) de cada zona: la búsqueda no puede caer fuera
AREA_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "ibiza": (1.15, 38.80, 1.65, 39.13),
    "formentera": (1.37, 38.63, 1.60, 38.77),
    "mallorca": (2.30, 39.25, 3.50, 39.97),
    "menorca": (3.78, 39.79, 4.33, 40.10),
    "barcelona": (2.05, 41.31, 2.23, 41.47),
    "barcelona_area": (1.70, 41.20, 2.50, 41.75),
}
AREA_REGION = {"ibiza": "Illes Balears", "formentera": "Illes Balears", "mallorca": "Illes Balears",
               "menorca": "Illes Balears", "barcelona": "Barcelona", "barcelona_area": "Barcelona"}
# radio de búsqueda de servicios: en ciudad todo está más cerca
RADIUS_M = {"barcelona": 700, "barcelona_area": 1000}
DEFAULT_RADIUS_M = 1500
BEACH_RADIUS_M = 3000
NEAREST_PER_CATEGORY = 3
MAX_PHOTOS = 6
_NOT_A_PHOTO = re.compile(r"\b(mapa?|map|plano|logo|escudo|coat of arms|flag|bandera|locator|situaci[oó]n)\b|\.svg$",
                          re.I)

# clave → (etiqueta, nombre para un elemento sin nombre)
CATEGORIES: dict[str, tuple[str, str]] = {
    "super": ("Supermercados", "Supermercado"),
    "farmacia": ("Farmacias", "Farmacia"),
    "salud": ("Centros de salud y clínicas", "Centro de salud"),
    "educacion": ("Colegios y guarderías", "Colegio"),
    "bus": ("Paradas de autobús", "Parada de autobús"),
    "tren": ("Tren, metro y tranvía", "Estación"),
    "ferri": ("Barco y ferri", "Embarcadero"),
    "restaurantes": ("Restaurantes, cafés y bares", "Restaurante"),
    "ocio": ("Ocio y cultura", "Ocio"),
    "deporte": ("Deporte", "Instalación deportiva"),
    "parques": ("Parques", "Parque"),
    "playas": ("Playas", "Playa"),
}
_RULES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("super", "shop", frozenset({"supermarket", "convenience"})),
    ("farmacia", "amenity", frozenset({"pharmacy"})),
    ("salud", "amenity", frozenset({"clinic", "doctors", "hospital"})),
    ("salud", "healthcare", frozenset({"centre", "clinic", "hospital"})),
    ("educacion", "amenity", frozenset({"school", "kindergarten"})),
    ("bus", "highway", frozenset({"bus_stop"})),
    ("tren", "railway", frozenset({"station", "halt", "tram_stop"})),
    ("ferri", "amenity", frozenset({"ferry_terminal"})),
    ("restaurantes", "amenity", frozenset({"restaurant", "cafe", "bar"})),
    ("ocio", "amenity", frozenset({"cinema", "theatre", "library", "arts_centre"})),
    ("deporte", "leisure", frozenset({"sports_centre", "fitness_centre"})),
    ("parques", "leisure", frozenset({"park"})),
    ("playas", "natural", frozenset({"beach"})),
)


class SourceError(Exception):
    """Una fuente externa no respondió como se esperaba."""


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str


def classify(tags: dict) -> str | None:
    for category, key, values in _RULES:
        if tags.get(key) in values:
            return category
    return None


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Distancia en línea recta (haversine), en metros."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * 6_371_000 * math.asin(math.sqrt(a)))


def _coords(element: dict) -> tuple[float, float] | None:
    point = element if "lat" in element else element.get("center") or {}
    try:
        return float(point["lat"]), float(point["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def summarise_pois(elements: list[dict], lat: float, lon: float) -> dict[str, dict]:
    """Por categoría: cuántos hay y los más cercanos (nombre y distancia)."""
    found: dict[str, list[dict]] = {key: [] for key in CATEGORIES}
    for element in elements:
        tags = element.get("tags") or {}
        category, point = classify(tags), _coords(element)
        if category is None or point is None:
            continue
        name = " ".join(str(tags.get("name") or "").split())[:80] or CATEGORIES[category][1]
        found[category].append({"name": name, "distance_m": distance_m(lat, lon, *point)})
    summary = {}
    for category, items in found.items():
        items.sort(key=lambda item: item["distance_m"])
        summary[category] = {"label": CATEGORIES[category][0], "count": len(items),
                             "nearest": items[:NEAREST_PER_CATEGORY]}
    return summary


def overpass_query(lat: float, lon: float, radius: int) -> str:
    around = f"(around:{radius},{lat:.5f},{lon:.5f})"
    beach = f"(around:{BEACH_RADIUS_M},{lat:.5f},{lon:.5f})"
    selectors = [
        f'nwr["shop"~"^(supermarket|convenience)$"]{around};', f'nwr["amenity"="pharmacy"]{around};',
        f'nwr["amenity"~"^(clinic|doctors|hospital)$"]{around};', f'nwr["healthcare"~"^(centre|clinic|hospital)$"]{around};',
        f'nwr["amenity"~"^(school|kindergarten)$"]{around};', f'node["highway"="bus_stop"]{around};',
        f'nwr["railway"~"^(station|halt|tram_stop)$"]{around};', f'nwr["amenity"="ferry_terminal"]{beach};',
        f'nwr["amenity"~"^(restaurant|cafe|bar)$"]{around};',
        f'nwr["amenity"~"^(cinema|theatre|library|arts_centre)$"]{around};',
        f'nwr["leisure"~"^(sports_centre|fitness_centre|park)$"]{around};',
        f'nwr["natural"="beach"]{beach};',
    ]
    return f"[out:json][timeout:60];({''.join(selectors)});out center tags 400;"


def _series(title: str) -> str:
    """Nombre de un archivo sin números, extensión ni sufijos: «Puerto 1.jpg» y «Puerto 2.jpg» son la misma serie."""
    base = norm(title.removeprefix("File:").rsplit(".", 1)[0])
    return re.sub(r"[\d\W_]+", " ", base).strip()[:24]


def _clean_html(value: str, limit: int) -> str:
    return " ".join(BeautifulSoup(value or "", "lxml").get_text(" ", strip=True).split())[:limit]


class AreaSources:
    """Consultas a las fuentes públicas. `http` es un objeto con get/post al estilo de requests."""

    def __init__(self, http=None, retry_pause: float | None = None) -> None:
        self.http = http or requests.Session()
        # con un cliente inyectado (pruebas) no se espera entre vueltas
        self.retry_pause = retry_pause if retry_pause is not None else (OVERPASS_RETRY_PAUSE_SECONDS if http is None else 0)
        self.headers = {"User-Agent": USER_AGENT, "Accept-Language": "es"}

    def _json(self, method: str, url: str, timeout: int = TIMEOUT_SECONDS, **kwargs):
        try:
            response = getattr(self.http, method)(url, headers=self.headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise SourceError(f"{url.split('/')[2]}: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise SourceError(f"{url.split('/')[2]}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"{url.split('/')[2]}: respuesta no válida") from exc

    def geocode(self, query: str, area: str) -> Location | None:
        west, south, east, north = AREA_BOUNDS[area]
        params = {"q": f"{query}, {AREA_REGION[area]}, España", "format": "jsonv2", "limit": 1, "countrycodes": "es",
                  "viewbox": f"{west},{north},{east},{south}", "bounded": 1}
        results = self._json("get", NOMINATIM_URL, params=params)
        if not results:
            return None
        first = results[0]
        try:
            return Location(float(first["lat"]), float(first["lon"]), str(first.get("display_name") or query)[:200])
        except (KeyError, TypeError, ValueError):
            return None

    def overpass(self, query: str) -> dict:
        """Consulta Overpass probando los servidores espejo; si todos fallan, otra vuelta tras una pausa."""
        last_error: SourceError | None = None
        for round_number in range(OVERPASS_ROUNDS):
            if round_number and self.retry_pause:
                time.sleep(self.retry_pause)
            for url in OVERPASS_URLS:
                try:
                    return self._json("post", url, timeout=OVERPASS_TIMEOUT_SECONDS, data={"data": query})
                except SourceError as exc:
                    log.info("Overpass no disponible (%s); se prueba otro servidor", exc)
                    last_error = exc
        raise last_error or SourceError("Overpass: sin servidores")

    def nearby(self, lat: float, lon: float, area: str) -> dict[str, dict]:
        payload = self.overpass(overpass_query(lat, lon, RADIUS_M.get(area, DEFAULT_RADIUS_M)))
        return summarise_pois(payload.get("elements") or [], lat, lon)

    def wikipedia(self, lat: float, lon: float, names: list[str]) -> dict | None:
        found = self._json("get", WIKIPEDIA_API, params={
            "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": 5000, "gslimit": 10,
            "format": "json"})
        articles = (found.get("query") or {}).get("geosearch") or []
        if not articles:
            return None
        wanted = [norm(n) for n in names if n]
        chosen = next((a for w in wanted for a in articles if norm(a.get("title", "")) == w), None) or \
            next((a for w in wanted for a in articles if w and w in norm(a.get("title", ""))), None) or articles[0]
        pages = self._json("get", WIKIPEDIA_API, params={
            "action": "query", "pageids": chosen["pageid"], "prop": "extracts|info", "exintro": 1, "explaintext": 1,
            "exchars": 1200, "inprop": "url", "format": "json"})
        page = next(iter(((pages.get("query") or {}).get("pages") or {}).values()), None)
        if not page or not page.get("extract"):
            return None
        url = str(page.get("fullurl") or "")
        return {"title": str(page.get("title") or "")[:120], "extract": " ".join(str(page["extract"]).split())[:1200],
                "url": url if url.startswith(SAFE_PAGE_PREFIXES) else ""}

    def photos(self, lat: float, lon: float, area: str) -> list[dict]:
        found = self._json("get", COMMONS_API, params={
            "action": "query", "generator": "geosearch", "ggscoord": f"{lat}|{lon}",
            "ggsradius": RADIUS_M.get(area, DEFAULT_RADIUS_M), "ggsnamespace": 6, "ggslimit": 25,
            "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 640, "format": "json"})
        photos, series = [], set()
        for page in ((found.get("query") or {}).get("pages") or {}).values():
            title = str(page.get("title") or "")
            info = (page.get("imageinfo") or [{}])[0]
            src, link = str(info.get("thumburl") or ""), str(info.get("descriptionurl") or "")
            if _NOT_A_PHOTO.search(title) or not src.startswith(SAFE_IMAGE_PREFIXES) or \
                    not link.startswith(SAFE_PAGE_PREFIXES):
                continue
            serie = _series(title)
            if serie in series:  # varias fotos de la misma serie: basta con una
                continue
            series.add(serie)
            meta = info.get("extmetadata") or {}
            photos.append({
                "src": src, "page": link,
                "title": title.removeprefix("File:").rsplit(".", 1)[0][:100],
                "author": _clean_html((meta.get("Artist") or {}).get("value", ""), 80) or "autor en Commons",
                "license": _clean_html((meta.get("LicenseShortName") or {}).get("value", ""), 40),
            })
            if len(photos) >= MAX_PHOTOS:
                break
        return photos
