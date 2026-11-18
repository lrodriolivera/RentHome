"""Descubrimiento de inmobiliarias locales nuevas.

1. Se reúnen webs candidatas reales (OpenStreetMap y búsqueda web, ver agency_finder; o pegadas por
   el administrador). El modelo ya no las sugiere: inventaba dominios.
2. Cada una se visita: el modelo decide si es una inmobiliaria local con alquiler de larga
   duración y cuál es su página de listado.
3. Se deduce el patrón de las fichas y se prueba el rastreo de verdad.
4. Lo que funciona queda como candidata para que el administrador la apruebe. Nada se
   añade solo.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import yaml
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..crawl import discover
from ..netguard import normalise_site
from ..parse import extract, norm
from .ai import LLM, LLMUnavailable, as_text
from .models import Agency, AgencyCandidate

log = logging.getLogger("renthome.discovery")

# Portales y agregadores: nunca son candidatos
PORTAL_DOMAINS = (
    "idealista", "fotocasa", "habitaclia", "pisos.com", "yaencontre", "milanuncios", "kyero", "thinkspain",
    "tucasa", "nuroa", "trovimap", "enalquiler", "indomio", "properstar", "spainhouses", "rentola", "nestoria",
    "trovit", "mitula", "wallapop", "airbnb", "booking", "spotahome", "housinganywhere", "badi", "uniplaces",
    "facebook", "instagram", "linkedin", "youtube", "twitter", "google", "wikipedia", "paginasamarillas",
    "realadvisor", "engelvoelkers", "remax", "century21", "tecnocasa", "donpiso", "housfy", "kelify",
    "gabinohome", "locanto", "hogaria", "casasenventaypisosalquiler", "habitania", "eyeonspain", "kuhamia",
)
RENTAL_HINTS = ("alquil", "lloguer", "rent", "larga", "anual", "llarga", "long")
LONG_TERM_HINTS = ("anual", "larga", "llarga", "long-term", "longterm", "long term", "larga-estancia",
                   "larga-duracion", "residencial", "year")
MAX_LINKS_FOR_MODEL = 60
MIN_DETAIL_LINKS = 3
PROBE_DETAILS = 3
MIN_RENT, MAX_RENT = 300, 20000
MAX_BULK_SITES = 25                   # webs pegadas que se analizan de una vez
_SITE_SEPARATORS = re.compile(r"[\s,;]+")
TOWN_LIMIT = 10                       # webs nuevas que se analizan como mucho en una búsqueda por ciudad
TOWN_MIN, TOWN_MAX = 2, 60
_TOWN_CHARS = re.compile(r"[^\W\d_]+(?:[ '’.\-][^\W\d_]*)*")
_TOWN_PARTICLES = {"de", "del", "d", "la", "las", "les", "los", "el", "els", "i", "y", "sa", "ses", "es", "s", "l"}

CLASSIFY_SYSTEM = """Analizas la página de inicio de una web inmobiliaria española. Responde SOLO JSON:
{
 "es_inmobiliaria_local": true/false,   // agencia con oficina en la zona; false si es portal, agregador, franquicia nacional, promotora o solo alquiler vacacional
 "nombre": "nombre comercial",
 "municipio": "municipio de la oficina o cadena vacía",
 "enlace_alquiler": número o null,      // índice del enlace que lleva al LISTADO de alquiler de larga duración/anual de viviendas; null si no hay
 "motivo": "una frase"
}
Elige el enlace más específico de alquiler residencial de larga duración. No elijas alquiler vacacional ni venta."""


@dataclass(frozen=True)
class Proposal:
    entry: dict
    evidence: str
    probe: str
    viable: bool


def site_key(url: str) -> str:
    """Dominio sin www, para comparar webs."""
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_portal(url: str) -> bool:
    key = site_key(url)
    return not key or any(domain in key for domain in PORTAL_DOMAINS)


def same_site_links(html: str, base: str) -> list[tuple[str, str]]:
    """(texto, url absoluta) de los enlaces internos, sin repetir."""
    soup, key, seen, out = BeautifulSoup(html, "lxml"), site_key(base), set(), []
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"].strip()).split("#")[0]
        if not url.startswith("http") or site_key(url) != key or url in seen:
            continue
        seen.add(url)
        out.append((" ".join(a.get_text(" ", strip=True).split())[:60], url))
    return out


# ---------- deducción del patrón de las fichas ----------

def _generalise(segment: str) -> str:
    if re.search(r"\d", segment):
        return "#"                      # ids, referencias
    if len(segment) > 24 or segment.count("-") >= 3:
        return "*"                      # títulos convertidos en dirección
    return segment


def _segment_regex(token: str) -> str:
    return {"#": r"[^/]*\d[^/]*", "*": r"[^/]+"}.get(token, re.escape(token))


def infer_detail_pattern(links: list[str], listing_url: str) -> tuple[str, int]:
    """Agrupa los enlaces por «forma» y devuelve el regex del grupo que parece de fichas."""
    groups: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for url in links:
        parsed = urlparse(url)
        if url.rstrip("/") == listing_url.rstrip("/"):
            continue
        segments = [s for s in parsed.path.split("/") if s]
        shape = tuple(_generalise(s) for s in segments)
        if parsed.query and re.search(r"\d", parsed.query):
            shape = (*shape, "?#")
        if any(token in ("#", "*", "?#") for token in shape):
            groups[shape].add(url)
    if not groups:
        return "", 0
    shape, urls = max(groups.items(), key=lambda item: len(item[1]))
    if len(urls) < MIN_DETAIL_LINKS:
        return "", len(urls)
    path = "/".join(_segment_regex(t) for t in shape if t != "?#")
    tail = r"\?.*\d" if shape and shape[-1] == "?#" else r"/?$"
    return rf"{re.escape(site_key(listing_url))}/{path}{tail}", len(urls)


def infer_page_pattern(links: list[str], listing_url: str) -> str:
    first = next((s for s in urlparse(listing_url).path.split("/") if s), "")
    pattern = rf"{re.escape(site_key(listing_url))}/{re.escape(first)}.*(?:page|pag|pagina|pg|p)[=/]\d+"
    return pattern if any(re.search(pattern, url) for url in links) else ""


# ---------- análisis de una web ----------

def classify(llm: LLM, home_url: str, html: str) -> tuple[dict, list[tuple[str, str]]]:
    links = same_site_links(html, home_url)
    ranked = sorted(links, key=lambda link: not any(h in norm(f"{link[0]} {link[1]}") for h in RENTAL_HINTS))
    shortlist = ranked[:MAX_LINKS_FOR_MODEL]
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = " ".join(soup.get_text(" ", strip=True).split())[:1800]
    listing = "\n".join(f"{i}. {label or '(sin texto)'} → {url}" for i, (label, url) in enumerate(shortlist))
    answer = llm.chat_json(CLASSIFY_SYSTEM, f"WEB: {home_url}\nTÍTULO: {title}\nTEXTO: {text}\n\nENLACES:\n{listing}",
                           max_tokens=300)
    return answer, shortlist


def probe(fetcher, entry: dict) -> tuple[int, int, int]:
    """(fichas encontradas, fichas leídas, con precio de alquiler plausible)."""
    urls, _badges = discover(fetcher, {**entry, "max_pages": 1})
    read = plausible = 0
    for url in urls[:PROBE_DETAILS]:
        html = fetcher.get(url, render=False)
        if not html:
            continue
        read += 1
        item = extract(html, url, entry["name"])
        if item.price and MIN_RENT <= item.price <= MAX_RENT and item.operation == "rent":
            plausible += 1
    return len(urls), read, plausible


def analyse_site(llm: LLM, fetcher, url: str, area: str) -> Proposal:
    """Visita una web y propone cómo rastrearla. No guarda nada."""
    home = normalise_site(url)
    if home is None:
        return Proposal({}, "", "no es la dirección de una web pública", False)
    html = fetcher.get(home) or (fetcher.render(home) if fetcher.render_available() else None)
    if not html:
        return Proposal({}, "", "la web no responde", False)
    try:
        answer, links = classify(llm, home, html)
    except LLMUnavailable as exc:
        return Proposal({}, "", f"IA no disponible: {exc}"[:200], False)
    name = as_text(answer.get("nombre"), 120) or site_key(home)
    evidence = as_text(answer.get("motivo"), 300)
    index = answer.get("enlace_alquiler")
    if answer.get("es_inmobiliaria_local") is not True:
        return Proposal({"name": name, "website": home}, evidence, "no es una inmobiliaria local", False)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(links):
        return Proposal({"name": name, "website": home}, evidence, "no se encontró un listado de alquiler de larga duración", False)

    label, listing_url = links[index]
    render = False
    listing_html = fetcher.get(listing_url)
    listing_links = [u for _t, u in same_site_links(listing_html, listing_url)] if listing_html else []
    pattern, found = infer_detail_pattern(listing_links, listing_url)
    if not pattern and fetcher.render_available():  # quizá el listado se pinta con JavaScript
        rendered = fetcher.render(listing_url)
        if rendered:
            listing_links = [u for _t, u in same_site_links(rendered, listing_url)]
            pattern, found = infer_detail_pattern(listing_links, listing_url)
            render = bool(pattern)
    entry = {
        "name": name, "town": as_text(answer.get("municipio"), 80), "areas": [area], "website": home,
        "start_urls": [listing_url], "detail_pattern": pattern, "max_pages": 3, "render": render,
        "long_term_section": any(h in norm(f"{label} {listing_url}") for h in LONG_TERM_HINTS),
    }
    page_pattern = infer_page_pattern(listing_links, listing_url)
    if page_pattern:
        entry["page_pattern"] = page_pattern
    if not pattern:
        return Proposal(entry, evidence, f"no se reconocen las fichas en {listing_url} ({found} enlaces parecidos)", False)
    try:
        total, read, plausible = probe(fetcher, entry)
    except Exception as exc:  # regex deducido inválido, red…
        return Proposal(entry, evidence, f"la prueba de rastreo falló: {exc}"[:200], False)
    viable = total >= MIN_DETAIL_LINKS and plausible >= max(1, read // 2)
    return Proposal(entry, evidence, f"{total} fichas; {plausible} de {read} con precio de alquiler plausible", viable)


# ---------- candidatas ----------

def known_sites(db: Session) -> set[str]:
    sites = {site_key(w) for w in db.scalars(select(Agency.website)) if w}
    return sites | {site_key(w) for w in db.scalars(select(AgencyCandidate.website)) if w}


def store(db: Session, proposal: Proposal, url: str, area: str, town: str = "") -> AgencyCandidate:
    """Guarda el resultado del análisis (también los inviables, para no repetirlos cada semana)."""
    website = f"https://{site_key(url)}"
    candidate = db.scalar(select(AgencyCandidate).where(AgencyCandidate.website == website)) or AgencyCandidate(website=website)
    candidate.name = str(proposal.entry.get("name") or site_key(url))[:120]
    candidate.area = area
    candidate.town = town or candidate.town
    candidate.evidence = proposal.evidence[:400]
    candidate.proposal = yaml.safe_dump(proposal.entry, allow_unicode=True, sort_keys=False) if proposal.entry else ""
    candidate.probe_result = proposal.probe[:400]
    candidate.status = "pending" if proposal.viable else "discarded"
    db.add(candidate)
    return candidate


def parse_sites(text: str) -> tuple[list[str], list[str]]:
    """Webs pegadas (una por línea, o separadas por comas). Devuelve (válidas sin repetir, rechazadas)."""
    valid: dict[str, str] = {}
    rejected: list[str] = []
    for token in _SITE_SEPARATORS.split(text or ""):
        token = token.strip()[:200]
        if not token:
            continue
        site = normalise_site(token)
        if site is None or is_portal(site):
            rejected.append(token)
        elif site_key(site) not in valid and len(valid) < MAX_BULK_SITES:
            valid[site_key(site)] = site
    return list(valid.values()), rejected


def clean_town(raw: str) -> str:
    """Nombre de ciudad o municipio legible. Solo letras, espacios, apóstrofos, puntos y guiones."""
    town = " ".join(str(raw or "").split())
    if not TOWN_MIN <= len(town) <= TOWN_MAX or not _TOWN_CHARS.fullmatch(town):
        raise ValueError("Escribe el nombre de una ciudad o municipio, por ejemplo «Sant Cugat del Vallès».")
    if town != town.lower():
        return town  # respeta cómo lo ha escrito quien sabe el nombre
    words = []
    for i, word in enumerate(town.split(" ")):
        if i and word in _TOWN_PARTICLES:
            words.append(word)
        else:
            words.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
    return " ".join(words)


@dataclass(frozen=True)
class TownResult:
    suggested: int
    analysed: int
    viable: int
    sources: str = ""     # de dónde salieron las webs, legible


def _analyse_new(factory: sessionmaker[Session], llm: LLM, fetcher, suggestions: list[str], area: str,
                 limit: int, town: str = "") -> tuple[int, int]:
    """Analiza las webs sugeridas que aún no se conocen. Devuelve (analizadas, viables)."""
    with factory() as db:
        known = known_sites(db)
    fresh = [u for u in dict.fromkeys(suggestions) if site_key(u) not in known][:limit]
    viable = 0
    for url in fresh:
        proposal = analyse_site(llm, fetcher, url, area)
        with factory() as db:
            store(db, proposal, url, area, town)
            db.commit()
        viable += 1 if proposal.viable else 0
    return len(fresh), viable


def _sources_line(found) -> str:
    line = f"OpenStreetMap: {found.osm} · web: {found.web}"
    return f"{line} ({found.notes})"[:200] if found.notes else line


def run(factory: sessionmaker[Session], llm: LLM, fetcher, areas: list[str], finder, per_area: int = 6) -> int:
    """Descubrimiento de toda la zona. Devuelve cuántas candidatas viables nuevas ha dejado."""
    viable = 0
    for area in areas:
        found = finder.find(area, "")
        analysed, new = _analyse_new(factory, llm, fetcher, found.sites, area, per_area)
        log.info("Descubrimiento %s: %d webs (%s), %d analizadas", area, len(found.sites), _sources_line(found), analysed)
        viable += new
    return viable


def run_town(factory: sessionmaker[Session], llm: LLM, fetcher, area: str, town: str, finder,
             limit: int = TOWN_LIMIT) -> TownResult:
    """Busca inmobiliarias de una ciudad concreta. LLMUnavailable se propaga a quien llama."""
    found = finder.find(area, town)
    analysed, viable = _analyse_new(factory, llm, fetcher, found.sites, area, limit, town)
    log.info("Descubrimiento en %s (%s): %d webs, %d analizadas, %d viables",
             town, area, len(found.sites), analysed, viable)
    return TownResult(suggested=len(found.sites), analysed=analysed, viable=viable, sources=_sources_line(found))
