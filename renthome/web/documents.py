"""Almacén de documentos cifrados en disco."""
from __future__ import annotations

import secrets
from pathlib import Path

from . import crypto
from .config import MAX_UPLOAD_BYTES, Settings
from .models import Document

PURPOSE = "document"
# tipo → firma con la que debe empezar el archivo
ALLOWED_TYPES: dict[str, tuple[str, bytes]] = {
    "pdf": ("application/pdf", b"%PDF-"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
}


class UploadError(ValueError):
    """El archivo no se acepta; el mensaje se muestra al usuario."""


def detect_mime(content: bytes) -> str:
    for mime, magic in ALLOWED_TYPES.values():
        if content.startswith(magic):
            return mime
    raise UploadError("Solo se aceptan archivos PDF, JPG o PNG.")


def safe_filename(name: str) -> str:
    base = Path(name or "documento").name
    cleaned = "".join(c for c in base if c.isalnum() or c in " ._-()").strip(" .")
    return (cleaned or "documento")[:160]


def validate(content: bytes) -> str:
    if not content:
        raise UploadError("El archivo está vacío.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadError(f"El archivo supera los {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return detect_mime(content)


def _path(settings: Settings, storage_name: str) -> Path:
    return settings.uploads_dir / storage_name


def store(settings: Settings, user_id: int, kind: str, filename: str, content: bytes) -> Document:
    mime = validate(content)
    storage_name = secrets.token_hex(24)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    _path(settings, storage_name).write_bytes(crypto.encrypt(settings.secret_key, user_id, PURPOSE, content))
    return Document(user_id=user_id, kind=kind, filename=safe_filename(filename), mime=mime,
                    size=len(content), storage_name=storage_name)


def read(settings: Settings, doc: Document) -> bytes:
    return crypto.decrypt(settings.secret_key, doc.user_id, PURPOSE, _path(settings, doc.storage_name).read_bytes())


def remove(settings: Settings, doc: Document) -> None:
    _path(settings, doc.storage_name).unlink(missing_ok=True)
