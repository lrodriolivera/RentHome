"""Configuración leída del entorno. Falla al arrancar si falta un secreto."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MIN_SECRET_LENGTH = 32
PRIVACY_VERSION = "2026-11-17b"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS_PER_USER = 40
# cuerpo máximo de una petición: subida de documentos / resto de formularios
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
MAX_FORM_REQUEST_BYTES = 64 * 1024
AUDIT_RETENTION_DAYS = 365
INVITATION_DAYS = 7
INACTIVE_DAYS = 365
SESSION_MAX_AGE_SECONDS = 14 * 24 * 3600


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    secret_key: str
    cookie_secure: bool
    controller_name: str
    controller_email: str
    base_url: str = ""
    trust_cf_header: bool = True
    crawl_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def load_settings() -> Settings:
    secret = os.environ.get("RENTHOME_SECRET_KEY", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"RENTHOME_SECRET_KEY debe tener al menos {MIN_SECRET_LENGTH} caracteres "
            "(genera una con: python -c 'import secrets;print(secrets.token_urlsafe(48))')"
        )
    root = Path(__file__).resolve().parent.parent.parent
    data_dir = Path(os.environ.get("RENTHOME_DATA_DIR") or root / "data-web")
    return Settings(
        data_dir=data_dir,
        secret_key=secret,
        cookie_secure=os.environ.get("RENTHOME_COOKIE_SECURE", "1") != "0",
        controller_name=os.environ.get("RENTHOME_CONTROLLER_NAME", "El administrador de RentHome"),
        controller_email=os.environ.get("RENTHOME_CONTROLLER_EMAIL", ""),
        base_url=os.environ.get("RENTHOME_BASE_URL", "").rstrip("/"),
        trust_cf_header=os.environ.get("RENTHOME_TRUST_CF_HEADER", "1") != "0",
        crawl_enabled=os.environ.get("RENTHOME_CRAWL_ENABLED", "1") != "0",
    )
