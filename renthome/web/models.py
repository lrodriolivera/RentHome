"""Modelos de datos. Los campos con sufijo _enc se guardan cifrados."""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_session_version() -> int:
    return secrets.randbelow(2**31 - 1) + 1


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    # AUTOINCREMENT: un id borrado no se reasigna (una cookie antigua no puede valer para otra persona)
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="user")  # user | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=new_session_version)
    consent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    consent_version: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    profile: Mapped["Profile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    preference: Mapped["SearchPreference | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    saved_searches: Mapped[list["SavedSearch"]] = relationship(cascade="all, delete-orphan")
    listing_states: Mapped[list["UserListing"]] = relationship(cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Profile(Base):
    """Datos que suelen pedir las inmobiliarias para un alquiler de larga duración."""
    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    nationality: Mapped[str] = mapped_column(String(60), default="")
    birth_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    id_type: Mapped[str] = mapped_column(String(12), default="")
    id_number_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    employment_status: Mapped[str] = mapped_column(String(24), default="")
    employer: Mapped[str] = mapped_column(String(120), default="")
    job_title: Mapped[str] = mapped_column(String(120), default="")
    employment_since: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    net_monthly_income: Mapped[int | None] = mapped_column(Integer, nullable=True)
    other_monthly_income: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_guarantor: Mapped[bool] = mapped_column(Boolean, default=False)
    adults: Mapped[int] = mapped_column(Integer, default=1)
    children: Mapped[int] = mapped_column(Integer, default=0)
    has_pets: Mapped[bool] = mapped_column(Boolean, default=False)
    pets_detail: Mapped[str] = mapped_column(String(200), default="")
    smoker: Mapped[bool] = mapped_column(Boolean, default=False)
    about: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")


class CriteriaColumns:
    """Columnas comunes a la búsqueda principal del perfil y a las búsquedas guardadas."""

    areas: Mapped[str] = mapped_column(String(200), default="")           # claves de catalog.AREAS separadas por coma
    zones: Mapped[str] = mapped_column(String(300), default="")           # barrios/pueblos preferidos, texto libre
    property_types: Mapped[str] = mapped_column(String(120), default="")  # claves de catalog.PROPERTY_TYPES
    max_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_bedrooms: Mapped[int] = mapped_column(Integer, default=1)
    preferred_bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_size_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    furnished: Mapped[str] = mapped_column(String(10), default="any")     # any | yes | no
    needs_pets_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_parking: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_elevator: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_terrace: Mapped[bool] = mapped_column(Boolean, default=False)
    # servicios deseados cerca (claves de catalog.SERVICES); opcionales, por eso admiten nulos
    wanted_services: Mapped[str | None] = mapped_column(String(160), nullable=True)
    services_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # True: ocultar los que no los tengan


class SearchPreference(CriteriaColumns, Base):
    """Qué vivienda busca la persona."""
    __tablename__ = "search_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    move_in_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="preference")


class Document(Base):
    """Metadatos de un documento; el contenido está cifrado en disco."""
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    filename: Mapped[str] = mapped_column(String(160))
    mime: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    storage_name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="documents")


class AuditLog(Base):
    """Registro de accesos y cambios sobre datos personales (sin contenido)."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(200), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")


class SavedSearch(CriteriaColumns, Base):
    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_viewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Agency(Base):
    """Inmobiliaria local y cómo se rastrea su web."""
    __tablename__ = "agencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    website: Mapped[str] = mapped_column(String(200), default="")
    town: Mapped[str] = mapped_column(String(80), default="")
    areas: Mapped[str] = mapped_column(String(200), default="")          # claves de catalog.AREAS
    start_urls: Mapped[str] = mapped_column(Text, default="")            # una URL por línea
    detail_pattern: Mapped[str] = mapped_column(String(300), default="")
    page_pattern: Mapped[str] = mapped_column(String(300), default="")
    max_pages: Mapped[int] = mapped_column(Integer, default=3)
    render: Mapped[bool] = mapped_column(Boolean, default=False)         # el listado necesita JavaScript
    render_details: Mapped[bool] = mapped_column(Boolean, default=False)
    long_term_section: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(12), default="admin")     # seed | admin | discovered
    notes: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_crawled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(200), default="")    # "" = bien; si no, el problema
    last_found: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    listings: Mapped[list["Listing"]] = relationship(back_populates="agency", cascade="all, delete-orphan")

    @property
    def area_list(self) -> list[str]:
        return [a for a in self.areas.split(",") if a]


class Listing(Base):
    """Anuncio rastreado. Compartido por todos los usuarios; no contiene datos personales."""
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    price: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)   # €/mes para alquiler anual
    price_prev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_type: Mapped[str] = mapped_column(String(12), default="")
    area: Mapped[str] = mapped_column(String(20), default="", index=True)
    place: Mapped[str] = mapped_column(String(60), default="")
    term: Mapped[str] = mapped_column(String(10), default="unknown")     # long | mixed | seasonal | unknown
    status: Mapped[str] = mapped_column(String(10), default="available")
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(String(300), default="")          # avisos o motivo de descarte
    furnished: Mapped[str] = mapped_column(String(3), default="")
    pets: Mapped[str] = mapped_column(String(3), default="")
    has_parking: Mapped[bool] = mapped_column(Boolean, default=False)
    has_elevator: Mapped[bool] = mapped_column(Boolean, default=False)
    has_terrace: Mapped[bool] = mapped_column(Boolean, default=False)
    image: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(String(600), default="")
    text: Mapped[str] = mapped_column(Text, default="")                  # texto de la ficha, para la IA
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    detail_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # capa de IA (fase 4): lectura del anuncio por el modelo local
    ai_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ai_summary: Mapped[str] = mapped_column(String(400), default="")
    ai_requirements: Mapped[str] = mapped_column(String(400), default="")
    ai_flags: Mapped[str] = mapped_column(String(300), default="")

    agency: Mapped[Agency] = relationship(back_populates="listings")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(12), default="scheduled")   # scheduled | user | admin
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued | running | done | failed
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(200), default="")


class UserListing(Base):
    """Lo que cada usuario ha hecho con un anuncio: guardarlo o descartarlo."""
    __tablename__ = "user_listings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(8))                       # saved | hidden
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    # capa de IA: encaje con el perfil y borrador de mensaje
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fit_reason: Mapped[str] = mapped_column(String(400), default="")   # en desuso: ahora va cifrado
    fit_reason_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    draft_message_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class AgencyCandidate(Base):
    """Inmobiliaria encontrada por el descubrimiento semanal, pendiente de revisión."""
    __tablename__ = "agency_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    website: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    area: Mapped[str] = mapped_column(String(20), default="")
    town: Mapped[str | None] = mapped_column(String(80), nullable=True)  # ciudad buscada, si vino de una búsqueda por ciudad
    evidence: Mapped[str] = mapped_column(String(400), default="")       # por qué parece una inmobiliaria local
    proposal: Mapped[str] = mapped_column(Text, default="")              # configuración propuesta (YAML)
    probe_result: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | approved | rejected
    found_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class TownSearch(Base):
    """Búsqueda de inmobiliarias en una ciudad concreta, lanzada por el administrador."""
    __tablename__ = "town_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    area: Mapped[str] = mapped_column(String(20))
    town: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued | running | done | failed
    suggested: Mapped[int] = mapped_column(Integer, default=0)   # webs encontradas (OSM + búsqueda web)
    analysed: Mapped[int] = mapped_column(Integer, default=0)    # nuevas que se visitaron
    viable: Mapped[int] = mapped_column(Integer, default=0)      # candidatas que se pueden rastrear
    sources: Mapped[str | None] = mapped_column(String(200), nullable=True)  # de dónde salieron las webs
    error: Mapped[str] = mapped_column(String(200), default="")
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AreaReport(Base):
    """Informe de la zona de un piso (datos públicos + resumen de la IA). Sin datos personales."""
    __tablename__ = "area_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued | running | done | failed
    locality: Mapped[str] = mapped_column(String(120), default="")      # sector buscado (p. ej. «Cala Llonga»)
    municipality: Mapped[str] = mapped_column(String(120), default="")
    locality_key: Mapped[str] = mapped_column(String(160), default="", index=True)  # zona|sector, para reutilizar
    services: Mapped[str | None] = mapped_column(String(200), nullable=True)  # servicios con algo cerca; None = sin datos
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    facts_json: Mapped[str] = mapped_column(Text, default="")             # datos de OSM, Wikipedia y Commons
    story_json: Mapped[str] = mapped_column(Text, default="")             # resumen redactado por la IA
    error: Mapped[str] = mapped_column(String(200), default="")           # o aviso, si el informe es parcial
    requested_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AppState(Base):
    """Pequeños valores del sistema (p. ej. cuándo se ejecutó el último descubrimiento)."""
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")
