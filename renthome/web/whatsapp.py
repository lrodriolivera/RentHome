"""Envío de resultados por WhatsApp a través del bridge del homelab (temporal, solo administradores).

El bridge (whatsapp-bridge) expone `POST /api/send` con autenticación Bearer y solo acepta
peticiones cuyo Host esté en su lista permitida (p. ej. `whatsapp-bridge-personal:8080`).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests
from sqlalchemy.orm import Session

from . import matching
from .models import AppState

MAX_LISTINGS_PER_MESSAGE = 15
MAX_RECENT = 5
SEND_TIMEOUT_SECONDS = 30
_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"\d{8,15}")


class WhatsAppError(Exception):
    """El bridge no aceptó el mensaje o no se pudo contactar."""


@dataclass(frozen=True)
class WhatsAppSender:
    url: str
    token: str

    @classmethod
    def from_env(cls) -> "WhatsAppSender | None":
        url = os.environ.get("RENTHOME_WHATSAPP_URL", "").strip().rstrip("/")
        token = os.environ.get("RENTHOME_WHATSAPP_TOKEN", "").strip()
        return cls(url=url, token=token) if url and token else None

    def send(self, recipient: str, message: str) -> None:
        try:
            response = requests.post(f"{self.url}/api/send", json={"recipient": recipient, "message": message},
                                     headers={"Authorization": f"Bearer {self.token}"}, timeout=SEND_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise WhatsAppError(exc.__class__.__name__) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not body.get("success"):
            raise WhatsAppError(str(body.get("message") or f"HTTP {response.status_code}")[:120])


def normalize_recipient(raw: str) -> str:
    """Número internacional solo con dígitos (con prefijo de país, sin '+' ni '00')."""
    value = _SEPARATORS.sub("", raw or "")
    if value.startswith("+"):
        value = value[1:]
    elif value.startswith("00"):
        value = value[2:]
    if not _PHONE.fullmatch(value):
        raise ValueError("Escribe un número de WhatsApp válido con el prefijo del país, p. ej. +34 600 11 22 33.")
    return value


def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _line(match: matching.Match) -> str:
    listing = match.listing
    price = f"{_money(listing.price)} €/mes" if listing.price else "precio a consultar"
    details = [price]
    if listing.bedrooms is not None:
        details.append(f"{listing.bedrooms} hab.")
    if listing.size_m2:
        details.append(f"{listing.size_m2} m²")
    new = "🆕 " if match.is_new else ""
    return f"{new}*{listing.title}*\n{' · '.join(details)} — {listing.agency.name}\n{listing.url}"


def build_message(title: str, matches: list[matching.Match], base_url: str, path: str, sender_name: str) -> str:
    total = len(matches)
    head = (f"🏠 *RentHome* · Enviado por {sender_name.strip() or 'un usuario de RentHome'}\n"
            f"Búsqueda «{title}»: {total} {'piso encaja' if total == 1 else 'pisos encajan'}")
    shown = matches[:MAX_LISTINGS_PER_MESSAGE]
    if total > len(shown):
        head += f" (estos son los {len(shown)} mejores)"
    parts = [head, *(_line(m) for m in shown)]
    if base_url:
        parts.append(f"Ver todos: {base_url}{path}")
    parts.append("Confirma siempre con la inmobiliaria el precio y el tipo de contrato.")
    parts.append(f"— Generado en RentHome · {base_url}" if base_url else "— Generado en RentHome")
    return "\n\n".join(parts)


def _recent_key(user_id: int) -> str:
    return f"wa_recent:{user_id}"


def recent_recipients(db: Session, user_id: int) -> list[str]:
    state = db.get(AppState, _recent_key(user_id))
    return [n for n in state.value.split(",") if n] if state and state.value else []


def remember_recipient(db: Session, user_id: int, recipient: str) -> None:
    numbers = [recipient, *(n for n in recent_recipients(db, user_id) if n != recipient)][:MAX_RECENT]
    state = db.get(AppState, _recent_key(user_id))
    if state is None:
        db.add(AppState(key=_recent_key(user_id), value=",".join(numbers)))
    else:
        state.value = ",".join(numbers)
