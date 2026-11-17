"""Características de una vivienda detectadas en el texto del anuncio.

Son indicios: que no aparezca "ascensor" no significa que no lo tenga.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..parse import norm

_SIZE = re.compile(r"(?<![\d.,])(\d{2,4})\s?(?:m2|m²|mts2|metros cuadrados|sqm|sq m)")
_UNFURNISHED = re.compile(r"sin amueblar|sin muebles|no amueblado|unfurnished|vacio de muebles")
_FURNISHED = re.compile(r"amueblad[oa]|amoblad[oa]|furnished|con muebles|equipad[oa] y amueblad")
_PARKING = re.compile(r"parking|garaje|garage|plaza de aparcamiento|aparcamiento")
_ELEVATOR = re.compile(r"ascensor|elevator|\blift\b")
_NO_ELEVATOR = re.compile(r"sin ascensor|no ascensor|no elevator|without (?:a )?lift|no lift")
_TERRACE = re.compile(r"terraza|balcon|terrace|balcony|patio|solarium")
_NO_PETS = re.compile(r"no (?:se )?(?:admiten?|aceptan?) (?:mascotas|animales)|no mascotas|sin mascotas|no pets|"
                      r"pets not allowed|mascotas no")
_PETS = re.compile(r"(?:se )?(?:admiten?|aceptan?) mascotas|mascotas (?:si|permitidas|bienvenidas)|pets allowed|"
                   r"pet friendly|pets welcome")

# orden de prioridad: lo más específico primero
_TYPES: list[tuple[str, re.Pattern[str]]] = [
    ("atico", re.compile(r"\b(atico|penthouse|atic)\b")),
    ("duplex", re.compile(r"\b(duplex|triplex)\b")),
    ("estudio", re.compile(r"\b(estudio|studio|loft)\b")),
    ("adosado", re.compile(r"\b(adosado|townhouse|pareado|casa adosada)\b")),
    ("casa", re.compile(r"\b(casa|chalet|chale|villa|finca|house|cottage|torre|masia)\b")),
    ("piso", re.compile(r"\b(piso|apartamento|apartment|flat|planta baja|bajo|apartament|pis)\b")),
]
MIN_SIZE_M2, MAX_SIZE_M2 = 15, 1500


@dataclass(frozen=True)
class Features:
    property_type: str
    size_m2: int | None
    furnished: str      # "yes" | "no" | ""
    pets: str           # "yes" | "no" | ""
    has_parking: bool
    has_elevator: bool
    has_terrace: bool


def detect(title: str, text: str) -> Features:
    ntitle, ntext = norm(title), norm(text)[:6000]
    both = f"{ntitle} {ntext}"
    property_type = next((key for key, rx in _TYPES if rx.search(ntitle)), "") or \
        next((key for key, rx in _TYPES if rx.search(ntext[:600])), "")
    size = next((int(m.group(1)) for m in _SIZE.finditer(both) if MIN_SIZE_M2 <= int(m.group(1)) <= MAX_SIZE_M2), None)
    furnished = "no" if _UNFURNISHED.search(both) else ("yes" if _FURNISHED.search(both) else "")
    pets = "no" if _NO_PETS.search(both) else ("yes" if _PETS.search(both) else "")
    return Features(
        property_type=property_type, size_m2=size, furnished=furnished, pets=pets,
        has_parking=bool(_PARKING.search(both)),
        has_elevator=bool(_ELEVATOR.search(both)) and not _NO_ELEVATOR.search(both),
        has_terrace=bool(_TERRACE.search(both)),
    )
