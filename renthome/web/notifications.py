"""Avisos por email de pisos nuevos en las búsquedas guardadas (opcional, si hay SMTP)."""
from __future__ import annotations

import datetime as dt
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import matching
from .models import SavedSearch, User, utcnow

log = logging.getLogger("renthome.notify")
MIN_HOURS_BETWEEN_EMAILS = 12
MAX_LISTINGS_PER_EMAIL = 12


@dataclass(frozen=True)
class Mailer:
    host: str
    port: int
    username: str
    password: str
    sender: str

    @classmethod
    def from_env(cls) -> "Mailer | None":
        host, user = os.environ.get("SMTP_HOST", ""), os.environ.get("SMTP_USER", "")
        if not host or not user:
            return None
        return cls(host=host, port=int(os.environ.get("SMTP_PORT") or 587), username=user,
                   password=os.environ.get("SMTP_PASSWORD", ""), sender=os.environ.get("MAIL_FROM") or user)

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, self.sender, to
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(message)


def build_body(search_name: str, matches: list[matching.Match], base_url: str, search_id: int) -> str:
    lines = [f"Hay {len(matches)} piso(s) nuevo(s) en tu búsqueda «{search_name}»:", ""]
    for match in matches[:MAX_LISTINGS_PER_EMAIL]:
        listing = match.listing
        price = f"{listing.price} €/mes" if listing.price else "precio a consultar"
        rooms = "" if listing.bedrooms is None else f" · {listing.bedrooms} hab."
        lines += [f"- {listing.title} ({price}{rooms}) — {listing.agency.name}", f"  {listing.url}"]
    if base_url:
        lines += ["", f"Ver todos: {base_url}/pisos?b={search_id}"]
    lines += ["", "Recibes este aviso porque lo activaste en una búsqueda guardada de RentHome.",
              "Puedes desactivarlo editando la búsqueda."]
    return "\n".join(lines)


def notify_saved_searches(factory: sessionmaker[Session], mailer: Mailer | None, base_url: str) -> int:
    """Envía, como mucho cada 12 h por búsqueda, los pisos aparecidos desde el último aviso."""
    if mailer is None:
        return 0
    sent = 0
    with factory() as db:
        now = utcnow()
        for search in db.scalars(select(SavedSearch).where(SavedSearch.notify)):
            since = search.last_notified_at or search.created_at
            if search.last_notified_at and now - search.last_notified_at < dt.timedelta(hours=MIN_HOURS_BETWEEN_EMAILS):
                continue
            user = db.get(User, search.user_id)
            if user is None or not user.is_active:
                continue
            fresh = [m for m in matching.find(db, search, user.id, new_since=since) if m.is_new]
            if not fresh:
                continue
            try:
                mailer.send(user.email, f"RentHome: {len(fresh)} piso(s) nuevo(s) en «{search.name}»",
                            build_body(search.name, fresh, base_url, search.id))
            except (smtplib.SMTPException, OSError) as exc:
                log.warning("No se pudo enviar el aviso: %s", exc)
                continue
            search.last_notified_at = now
            sent += 1
        db.commit()
    return sent
