"""Decide si un anuncio cumple los criterios y le asigna una puntuación."""
from __future__ import annotations

import re

from .parse import norm

DWELLING = (r"\b(piso|apartamento|apartment|flat|casa|house|home|estudio|studio|atico|penthouse|"
            r"chalet|chale|villa|duplex|bajo|planta baja|ground floor|adosado|townhouse|loft|"
            r"vivienda|cottage|finca|dormitorio|habitaciones|bedroom)s?\b")
# palabras que en un anuncio de vivienda solo indican un extra ("con parking")
SOFT_EXCLUDES = {"parking", "garaje", "plaza de aparcamiento", "trastero"}


def effective_price(d: dict) -> int | None:
    """Si el anuncio menciona expresamente un precio para alquiler anual, ese manda."""
    if d.get("annual_price") and d.get("term") in ("long", "mixed"):
        return d["annual_price"]
    return d.get("price")


def evaluate(d: dict, criteria: dict, long_term_section: bool) -> tuple[bool, list[str], int]:
    """Devuelve (cumple, motivos/avisos, puntuación)."""
    notes: list[str] = []

    if d.get("operation") == "sale":
        return False, ["es venta"], 0
    if d.get("status") in ("rented", "reserved"):
        return False, ["alquilado/reservado"], 0

    title = norm(d.get("title", "")) + " " + norm(d.get("url", "").rsplit("/", 1)[-1].replace("-", " "))
    # "piso con parking" es una vivienda: solo se excluye si no se menciona vivienda
    is_home = re.search(DWELLING, title) is not None
    for ex in criteria.get("exclude_types", []):
        if norm(ex) in title and not (is_home and norm(ex) in SOFT_EXCLUDES):
            return False, [f"tipo excluido: {ex}"], 0

    full = title + " " + norm(d.get("snippet", ""))
    for place in criteria.get("exclude_places", []):
        if norm(place) in full:
            return False, [f"fuera de Ibiza: {place}"], 0

    term = d.get("term", "unknown")
    if term == "seasonal":
        return False, ["temporada / no anual"], 0
    if term == "unknown" and not long_term_section:
        return False, ["no consta que sea anual"], 0
    if term == "mixed":
        notes.append("⚠️ menciona anual y temporada: confirmar contrato")
    if term == "unknown":
        notes.append("sección de alquiler anual de la inmobiliaria")

    price = effective_price(d)
    max_price = criteria.get("max_price", 2000)
    if price is None:
        if not criteria.get("include_unknown_price", True):
            return False, ["sin precio"], 0
        # sin precio publicado: casi siempre villas/fincas de lujo muy por encima del presupuesto
        if (d.get("bedrooms") or 0) > criteria.get("unknown_price_max_bedrooms", 3):
            return False, ["sin precio y muchas habitaciones (probable lujo)"], 0
        for w in criteria.get("luxury_words", []):
            if norm(w) in title:
                return False, [f"sin precio y '{w}' (probable lujo)"], 0
        notes.append("precio no publicado: consultar")
    elif price > max_price:
        return False, [f"precio {price} € > {max_price} €"], 0
    elif price < 300:
        return False, [f"precio sospechoso ({price} €)"], 0

    beds = d.get("bedrooms")
    minb = criteria.get("min_bedrooms", 1)
    maxb = criteria.get("max_bedrooms")
    if beds is None:
        if not criteria.get("include_unknown_bedrooms", True):
            return False, ["habitaciones desconocidas"], 0
        notes.append("nº de habitaciones no detectado")
    else:
        if beds < minb:
            return False, [f"{beds} hab. < mínimo {minb}"], 0
        if maxb is not None and beds > maxb:
            return False, [f"{beds} hab. > máximo {maxb}"], 0

    # puntuación: 2 habitaciones primero, luego 3, luego 1; más barato mejor
    pref = criteria.get("preferred_bedrooms", 2)
    if beds == pref:
        score = 100
    elif beds is None:
        score = 55
    elif beds > pref:
        score = 80 - 5 * (beds - pref - 1)
    else:
        score = 60
    if price:
        score += max(0, (max_price - price) // 100)  # hasta +17 aprox
    if term == "long":
        score += 5
    return True, notes, score
