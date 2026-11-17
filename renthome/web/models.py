"""Modelos de datos. Los campos con sufijo _enc se guardan cifrados."""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text
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


class SearchPreference(Base):
    """Qué vivienda busca la persona."""
    __tablename__ = "search_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
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
