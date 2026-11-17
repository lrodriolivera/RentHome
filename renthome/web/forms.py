"""Lectura y validación de campos de formulario."""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,185}\.[^@\s]{2,24}$")


class FormError(ValueError):
    """Un campo no es válido; el mensaje se muestra al usuario."""


def text(form: Mapping, name: str, max_length: int, required: bool = False) -> str:
    value = " ".join(str(form.get(name) or "").split()) if max_length <= 300 else str(form.get(name) or "").strip()
    if required and not value:
        raise FormError("Falta un campo obligatorio.")
    if len(value) > max_length:
        raise FormError(f"Un campo supera los {max_length} caracteres.")
    return value


def email(form: Mapping, name: str = "email") -> str:
    value = str(form.get(name) or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise FormError("El email no es válido.")
    return value


def integer(form: Mapping, name: str, minimum: int, maximum: int, default: int | None = None) -> int | None:
    raw = str(form.get(name) or "").strip().replace(".", "").replace(" ", "")
    if not raw:
        return default
    if not raw.isdigit():
        raise FormError("Escribe solo números en los campos numéricos.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise FormError(f"Un valor numérico está fuera de rango ({minimum}–{maximum}).")
    return value


def date(form: Mapping, name: str) -> dt.date | None:
    raw = str(form.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise FormError("Una fecha no es válida.") from exc
    if not dt.date(1900, 1, 1) <= value <= dt.date(2100, 1, 1):
        raise FormError("Una fecha está fuera de rango.")
    return value


def choice(form: Mapping, name: str, options: Mapping[str, str], default: str = "") -> str:
    value = str(form.get(name) or "")
    return value if value in options else default


def choices(form, name: str, options: Mapping[str, str]) -> str:
    """Varias casillas → claves válidas separadas por coma."""
    return ",".join(v for v in form.getlist(name) if v in options)


def flag(form: Mapping, name: str) -> bool:
    return str(form.get(name) or "") == "1"
