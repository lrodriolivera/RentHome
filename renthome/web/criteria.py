"""Criterios de búsqueda: lectura del formulario y resumen legible."""
from __future__ import annotations

from . import catalog, forms

CRITERIA_FIELDS = ("areas", "zones", "property_types", "max_price", "min_bedrooms", "preferred_bedrooms",
                   "min_size_m2", "furnished", "needs_pets_allowed", "needs_parking", "needs_elevator",
                   "wants_terrace", "wanted_services", "services_required")


def read_form(form) -> dict:
    """Valida los campos comunes de una búsqueda. Lanza forms.FormError."""
    values = {
        "areas": forms.choices(form, "areas", catalog.AREAS),
        "zones": forms.text(form, "zones", 300),
        "property_types": forms.choices(form, "property_types", catalog.PROPERTY_TYPES),
        "max_price": forms.integer(form, "max_price", 200, 50_000),
        "min_bedrooms": forms.integer(form, "min_bedrooms", 0, 10, default=1),
        "preferred_bedrooms": forms.integer(form, "preferred_bedrooms", 0, 10),
        "min_size_m2": forms.integer(form, "min_size_m2", 10, 2000),
        "furnished": forms.choice(form, "furnished", catalog.FURNISHED, default="any"),
        "needs_pets_allowed": forms.flag(form, "needs_pets_allowed"),
        "needs_parking": forms.flag(form, "needs_parking"),
        "needs_elevator": forms.flag(form, "needs_elevator"),
        "wants_terrace": forms.flag(form, "wants_terrace"),
        "wanted_services": forms.choices(form, "wanted_services", catalog.SERVICES) or None,
        "services_required": forms.flag(form, "services_required"),
    }
    if not values["areas"]:
        raise forms.FormError("Elige al menos una zona donde buscar.")
    return values


def copy(source, target) -> None:
    for field in CRITERIA_FIELDS:
        setattr(target, field, getattr(source, field))


def wanted_services(criteria) -> list[str]:
    return [s for s in (getattr(criteria, "wanted_services", "") or "").split(",") if s in catalog.SERVICES]


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} y {words[-1]}"


def area_keys(criteria) -> list[str]:
    return [a for a in (criteria.areas or "").split(",") if a]


def summary(criteria) -> str:
    """Una línea: 'Ibiza, Mallorca · hasta 2.000 € · 2+ hab.'"""
    parts = [", ".join(catalog.AREAS[a] for a in area_keys(criteria) if a in catalog.AREAS) or "Sin zona"]
    if criteria.max_price:
        parts.append("hasta " + f"{criteria.max_price:,}".replace(",", ".") + " €")
    parts.append("estudios incluidos" if criteria.min_bedrooms == 0 else f"{criteria.min_bedrooms}+ hab.")
    types = [catalog.PROPERTY_TYPES[t].split(" /")[0].lower() for t in (criteria.property_types or "").split(",")
             if t in catalog.PROPERTY_TYPES]
    if types:
        parts.append(", ".join(types))
    services = [catalog.SERVICE_SHORT[s] for s in wanted_services(criteria)]
    if services:
        parts.append(f"{'solo' if criteria.services_required else 'mejor'} con {_join(services)} cerca")
    return " · ".join(parts)
