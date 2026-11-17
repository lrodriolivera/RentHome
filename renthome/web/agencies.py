"""Alta de inmobiliarias desde YAML (semilla inicial y propuestas del descubrimiento)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..netguard import looks_public
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog
from .models import Agency

SEED_FILE = Path(__file__).resolve().parent / "agencies_seed.yaml"
MAX_START_URLS = 12
MAX_PATTERN_LENGTH = 300
# cuantificador sobre un grupo que ya contiene otro: (a+)+, (.*)*… bloquean el rastreador
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*{]")


class AgencyConfigError(ValueError):
    """La configuración de rastreo no es válida; el mensaje se muestra al administrador."""


def validate_config(start_urls: list[str], detail_pattern: str, page_pattern: str) -> None:
    if not start_urls:
        raise AgencyConfigError("Indica al menos una página de listado.")
    if len(start_urls) > MAX_START_URLS:
        raise AgencyConfigError(f"Como máximo {MAX_START_URLS} páginas de listado.")
    for url in start_urls:
        if not looks_public(url):
            raise AgencyConfigError(f"No es la dirección de una web pública: {url[:80]}")
    if not detail_pattern:
        raise AgencyConfigError("Falta el patrón de las fichas.")
    for pattern in (detail_pattern, page_pattern):
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise AgencyConfigError(f"Un patrón supera los {MAX_PATTERN_LENGTH} caracteres.")
        if _NESTED_QUANTIFIER.search(pattern):
            raise AgencyConfigError("Patrón no admitido: evita repetir un grupo que ya contiene + o *.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise AgencyConfigError(f"Patrón no válido: {exc}") from exc


def apply_entry(agency: Agency, entry: dict) -> None:
    """Copia una entrada YAML (formato de config.yaml) a un Agency."""
    areas = entry.get("areas") or ([entry["area"]] if entry.get("area") else [])
    start_urls = [str(u).strip() for u in entry.get("start_urls") or [] if str(u).strip()]
    validate_config(start_urls, str(entry.get("detail_pattern") or ""), str(entry.get("page_pattern") or ""))
    agency.name = str(entry["name"])[:120]
    agency.website = str(entry.get("website") or "")[:200]
    agency.town = str(entry.get("town") or "")[:80]
    agency.areas = ",".join(a for a in areas if a in catalog.AREAS)
    agency.start_urls = "\n".join(start_urls)
    agency.detail_pattern = str(entry["detail_pattern"])[:300]
    agency.page_pattern = str(entry.get("page_pattern") or "")[:300]
    agency.max_pages = max(1, min(int(entry.get("max_pages") or 3), 20))
    agency.render = bool(entry.get("render", False))
    agency.render_details = bool(entry.get("render_details", False))
    agency.long_term_section = bool(entry.get("long_term_section", False))
    if not agency.areas:
        raise AgencyConfigError("Elige al menos una zona.")


def seed(db: Session, path: Path = SEED_FILE) -> int:
    """Añade las inmobiliarias del archivo que aún no existan (por nombre). Devuelve cuántas."""
    if not path.exists():
        return 0
    existing = set(db.scalars(select(Agency.name)))
    added = 0
    for entry in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        if entry.get("name") in existing:
            continue
        agency = Agency(origin="seed")
        apply_entry(agency, entry)
        db.add(agency)
        added += 1
    return added
