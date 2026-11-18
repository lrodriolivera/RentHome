"""Informe de la zona de un piso: dónde está, qué hay cerca y un resumen para decidir.

1. Se identifica el sector más concreto que menciona el anuncio (la IA propone; solo se acepta
   si el nombre aparece en el propio anuncio) y se ubica con OpenStreetMap dentro de su zona.
2. Se reúnen datos públicos: servicios cercanos (OSM), descripción (Wikipedia) y fotos (Commons).
3. La IA local redacta un resumen basado solo en esos datos. Sin IA, el informe se muestra igual.
Los informes se reutilizan para otros pisos del mismo sector.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..parse import norm
from . import catalog
from .ai import LLM, LLMUnavailable, as_text, interactive
from .area_sources import CATEGORIES, AreaSources, Location, SourceError
from .criteria import area_keys
from .models import AreaReport, Listing, SavedSearch, SearchPreference, User, utcnow

log = logging.getLogger("renthome.area")

MAX_REPORTS_PER_DAY = 15          # informes nuevos que puede pedir cada usuario al día
REFRESH_AFTER_DAYS = 14           # antes de esto no se ofrece actualizar un informe terminado
CACHE_DAYS = 30                   # un informe de un sector sirve para otros pisos durante este tiempo
AD_TEXT_FOR_LOCALITY = 1500
SCAN_BATCH = 10                   # pisos que se encolan en cada pasada del análisis automático
SCAN_INTERVAL_SECONDS = 15 * 60
SCAN_FIRST_DELAY_SECONDS = 60
TRANSPORT_CATEGORIES = ("bus", "tren", "ferri")

LOCALITY_SYSTEM = """Lees un anuncio de alquiler en España. Responde SOLO JSON:
{"lugar": "barrio, urbanización, cala o pueblo MÁS concreto donde está la vivienda, tal como aparece en el anuncio",
 "municipio": "municipio al que pertenece"}
Usa únicamente nombres que aparezcan en el anuncio. Si no hay nada más concreto que el municipio, repite el municipio."""

STORY_SYSTEM = """Eres un asesor local que ayuda a alguien a decidir si alquilar una vivienda (alquiler de larga
duración, para vivir todo el año). Con los DATOS DE LA ZONA describe el sector en español, de forma práctica y honesta.
Reglas: usa solo los datos dados y hechos generales muy conocidos del municipio; no inventes nombres, distancias,
horarios ni servicios; si un dato falta, dilo. Las distancias son en línea recta. Los datos de OpenStreetMap pueden
estar incompletos: si una categoría sale a 0, di que no aparece en el mapa (no afirmes que no existe). Responde SOLO JSON:
{"resumen": "2-3 frases", "ambiente": "cómo es vivir allí todo el año", "servicios": "compras, salud, colegios",
 "transporte": "transporte público y accesos", "ocio": "ocio, playas, parques, deporte",
 "a_favor": ["hasta 4 puntos"], "a_tener_en_cuenta": ["hasta 4 puntos"]}"""

STORY_TEXT_FIELDS = {"resumen": 600, "ambiente": 450, "servicios": 450, "transporte": 450, "ocio": 450}
STORY_LIST_FIELDS = ("a_favor", "a_tener_en_cuenta")
MAX_STORY_ITEMS = 4


class LocateError(Exception):
    """No se pudo ubicar el piso en el mapa."""


def locality_key(area: str, locality: str) -> str:
    return f"{area}|{norm(locality)}"[:160]


def services_from(pois: dict) -> str | None:
    """Servicios con algo en el radio («super,transporte…»). None si no hay datos de OpenStreetMap."""
    if not pois:
        return None
    present = {key for key, info in pois.items() if (info or {}).get("count")}
    if present & set(TRANSPORT_CATEGORIES):
        present = (present - set(TRANSPORT_CATEGORIES)) | {"transporte"}
    return ",".join(sorted(present & set(catalog.SERVICES)))


def locality_for(listing: Listing, llm: LLM | None, interactive_call: bool = True) -> tuple[str, str]:
    """(sector, municipio). El sector de la IA solo vale si aparece en el anuncio."""
    fallback = listing.place or catalog.AREAS.get(listing.area, listing.area)
    if llm is None:
        return fallback, fallback
    ad = f"{listing.title}\n{listing.place}\n{listing.snippet}\n{listing.text[:AD_TEXT_FOR_LOCALITY]}"
    try:
        with interactive() if interactive_call else nullcontext():
            answer = llm.chat_json(LOCALITY_SYSTEM, f"Indica el lugar más concreto.\nANUNCIO:\n{ad}", max_tokens=120)
    except LLMUnavailable:
        return fallback, fallback
    place = as_text(answer.get("lugar"), 100)
    town = as_text(answer.get("municipio"), 100) or fallback
    if len(norm(place)) < 3 or norm(place) not in norm(ad):
        return fallback, fallback
    return place, town


def _locate(sources: AreaSources, area: str, locality: str, municipality: str) -> Location:
    queries = [f"{locality}, {municipality}" if norm(locality) != norm(municipality) else locality, municipality]
    for query in dict.fromkeys(queries):
        location = sources.geocode(query, area)
        if location is not None:
            return location
    raise LocateError(f"No se pudo ubicar «{locality}» en el mapa.")


def gather(sources: AreaSources, area: str, locality: str, municipality: str) -> tuple[Location, dict]:
    """Datos públicos de la zona. Solo es imprescindible ubicarla; el resto se añade si responde."""
    location = _locate(sources, area, locality, municipality)
    facts: dict = {"label": location.label, "pois": {}, "wiki": None, "photos": [], "missing": []}
    steps = (("pois", "servicios cercanos", lambda: sources.nearby(location.lat, location.lon, area)),
             ("wiki", "Wikipedia", lambda: sources.wikipedia(location.lat, location.lon, [locality, municipality])),
             ("photos", "fotos", lambda: sources.photos(location.lat, location.lon, area)))
    for key, label, fetch in steps:
        try:
            facts[key] = fetch() or facts[key]
        except SourceError as exc:
            log.info("Informe de zona: %s no disponible (%s)", label, exc)
            facts["missing"].append(label)
    return location, facts


def facts_prompt(listing: Listing, locality: str, municipality: str, facts: dict) -> str:
    lines = [f"Vivienda: {listing.title} ({catalog.AREAS.get(listing.area, listing.area)})",
             f"Sector: {locality} · municipio: {municipality}", f"Ubicación aproximada: {facts.get('label', '')}", ""]
    for key, (label, _) in CATEGORIES.items():
        info = (facts.get("pois") or {}).get(key)
        if not info:
            continue
        nearest = ", ".join(f"{p['name']} ({p['distance_m']} m)" for p in info["nearest"]) or "ninguno cerca"
        lines.append(f"- {label}: {info['count']} en el radio · más cercanos: {nearest}")
    wiki = facts.get("wiki")
    if wiki:
        lines += ["", f"Wikipedia ({wiki['title']}): {wiki['extract']}"]
    if facts.get("missing"):
        lines += ["", f"Datos no disponibles: {', '.join(facts['missing'])}"]
    return "DATOS DE LA ZONA\n" + "\n".join(lines)


def narrate(llm: LLM, listing: Listing, locality: str, municipality: str, facts: dict) -> dict:
    with interactive():
        answer = llm.chat_json(STORY_SYSTEM, facts_prompt(listing, locality, municipality, facts), max_tokens=900)
    story = {field: as_text(answer.get(field), limit) for field, limit in STORY_TEXT_FIELDS.items()}
    for field in STORY_LIST_FIELDS:
        items = answer.get(field) if isinstance(answer.get(field), list) else []
        story[field] = [text for text in (as_text(item, 140) for item in items) if text][:MAX_STORY_ITEMS]
    if not story["resumen"]:
        raise LLMUnavailable("respuesta vacía")
    return story


def _cached(db: Session, key: str, exclude_id: int) -> AreaReport | None:
    since = utcnow() - dt.timedelta(days=CACHE_DAYS)
    return db.scalar(select(AreaReport).where(
        AreaReport.locality_key == key, AreaReport.status == "done", AreaReport.id != exclude_id,
        AreaReport.facts_json != "", AreaReport.updated_at >= since).order_by(AreaReport.updated_at.desc()))


def _start(factory: sessionmaker[Session], report_id: int) -> tuple[Listing, dict] | None:
    """Marca el informe en marcha. Devuelve el piso y lo que el informe ya tenía."""
    with factory() as db:
        report = db.get(AreaReport, report_id)
        listing = db.get(Listing, report.listing_id) if report else None
        if report is None or listing is None:
            return None
        own = {"auto": report.requested_by is None, "facts_json": report.facts_json, "story_json": report.story_json,
               "lat": report.lat, "lon": report.lon, "fresh": utcnow() - report.updated_at < dt.timedelta(days=CACHE_DAYS)}
        report.status, report.error, report.updated_at = "running", "", utcnow()
        db.commit()
        db.expunge(listing)
        return listing, own


def run_report(factory: sessionmaker[Session], report_id: int, sources: AreaSources, llm: LLM | None) -> None:
    """Prepara un informe. Los automáticos (sin usuario que los pida) no llevan resumen de la IA."""
    started = _start(factory, report_id)
    if started is None:
        return
    listing, own = started
    want_story = not own["auto"]
    locality, municipality = locality_for(listing, llm, interactive_call=want_story)
    key = locality_key(listing.area, locality)
    values: dict = {"locality": locality, "municipality": municipality, "locality_key": key, "error": ""}
    story: dict | None = None
    if own["facts_json"] and own["fresh"] and not own["story_json"]:
        # solo faltaba el resumen (p. ej. un informe automático): los datos se reutilizan
        values.update(lat=own["lat"], lon=own["lon"], facts_json=own["facts_json"])
    else:
        with factory() as db:
            previous = _cached(db, key, report_id)
            if previous is not None:
                values.update(lat=previous.lat, lon=previous.lon, facts_json=previous.facts_json)
                story = json.loads(previous.story_json) if previous.story_json else None
    try:
        if "facts_json" not in values:
            location, facts = gather(sources, listing.area, locality, municipality)
            values.update(lat=location.lat, lon=location.lon,
                          facts_json=json.dumps(facts, ensure_ascii=False))
        facts = json.loads(values["facts_json"])
        values["services"] = services_from(facts.get("pois") or {})
        if want_story and story is None and llm is not None:
            try:
                story = narrate(llm, listing, locality, municipality, facts)
            except LLMUnavailable as exc:
                log.info("Informe de zona sin resumen: %s", exc)
        if want_story and story is None:
            values["error"] = "El resumen de la IA no está disponible ahora; se muestran los datos de la zona."
        values.update(status="done", story_json=json.dumps(story, ensure_ascii=False) if story and want_story else "")
    except LocateError as exc:
        values.update(status="failed", error=str(exc))
    except Exception as exc:  # una fuente rara no debe dejar el informe colgado
        log.exception("Fallo preparando el informe de zona %s", report_id)
        values.update(status="failed", error=f"Error inesperado ({exc.__class__.__name__}). Prueba más tarde.")
    with factory() as db:
        report = db.get(AreaReport, report_id)
        if report is None:
            return
        for field, value in values.items():
            setattr(report, field, value[:200] if field == "error" else value)
        report.updated_at = utcnow()
        db.commit()


def wanted_areas(db: Session) -> set[str]:
    """Zonas de las búsquedas (de usuarios activos) que piden servicios cerca."""
    areas: set[str] = set()
    for model in (SearchPreference, SavedSearch):
        rows = db.scalars(select(model).join(User, User.id == model.user_id)
                          .where(User.is_active, model.wanted_services.is_not(None), model.wanted_services != ""))
        for criteria in rows:
            areas.update(area_keys(criteria))
    return areas


class AreaReportRunner:
    """Prepara los informes de uno en uno, fuera de la petición web."""

    def __init__(self, factory: sessionmaker[Session], llm: LLM | None, sources: AreaSources | None = None) -> None:
        self.factory, self.llm = factory, llm
        self.sources = sources or AreaSources()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="area-report")
        self._stop = threading.Event()
        self._wake = threading.Event()   # para analizar en cuanto alguien pide servicios, sin esperar

    def submit(self, report_id: int) -> None:
        self._pool.submit(self._run, report_id)

    def _run(self, report_id: int) -> None:
        try:
            run_report(self.factory, report_id, self.sources, self.llm)
        except Exception:
            log.exception("Fallo en el informe de zona %s", report_id)

    def scan_once(self) -> int:
        """Encola el análisis automático de pisos sin informe en las zonas donde alguien pide servicios."""
        with self.factory() as db:
            areas = wanted_areas(db)
            if not areas:
                return 0
            analysed = select(AreaReport.listing_id)
            listings = list(db.scalars(select(Listing.id).where(
                Listing.active, Listing.eligible, Listing.area.in_(areas), Listing.id.not_in(analysed))
                .order_by(Listing.first_seen.desc()).limit(SCAN_BATCH)))
            reports = [AreaReport(listing_id=listing_id, requested_by=None) for listing_id in listings]
            db.add_all(reports)
            db.commit()
            ids = [r.id for r in reports]
        for report_id in ids:
            self.submit(report_id)
        return len(ids)

    def wake(self) -> None:
        self._wake.set()

    def start_scanner(self) -> None:
        def loop() -> None:
            self._wake.wait(SCAN_FIRST_DELAY_SECONDS)
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    queued = self.scan_once()
                    if queued:
                        log.info("Análisis de zonas: %d pisos en cola", queued)
                except Exception:
                    log.exception("Fallo en el análisis automático de zonas")
                self._wake.wait(SCAN_INTERVAL_SECONDS)
        threading.Thread(target=loop, name="area-scanner", daemon=True).start()

    def resume(self) -> None:
        """Tras un reinicio, retoma los informes que quedaron a medias."""
        with self.factory() as db:
            pending = list(db.scalars(select(AreaReport.id).where(AreaReport.status.in_(("queued", "running")))))
        for report_id in pending:
            self.submit(report_id)

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def can_refresh(report: AreaReport) -> bool:
    if report.status == "failed" or (report.status == "done" and not report.story_json):
        return True  # falló, o falta el resumen de la IA
    return report.status == "done" and utcnow() - report.updated_at >= dt.timedelta(days=REFRESH_AFTER_DAYS)


def decode(report: AreaReport | None) -> tuple[dict, dict]:
    if report is None:
        return {}, {}
    try:
        facts = json.loads(report.facts_json) if report.facts_json else {}
        story = json.loads(report.story_json) if report.story_json else {}
    except ValueError:
        return {}, {}
    return facts, story
