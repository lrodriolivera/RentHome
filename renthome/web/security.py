"""Contraseñas, tokens de invitación y límite de intentos de acceso."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LIMITER_MAX_KEYS = 20_000
MAX_EMAIL_LENGTH = 254

_hasher = PasswordHasher()
# hash de relleno para que el tiempo de respuesta no revele si el email existe
_DUMMY_HASH = _hasher.hash("renthome-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
    if len(password) > MAX_PASSWORD_LENGTH:
        return "La contraseña es demasiado larga."
    if password.isdigit() or password.isalpha():
        return "Combina letras con números o símbolos."
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Bloquea temporalmente tras varios intentos fallidos (por IP y por email)."""

    def __init__(self, max_failures: int = LOGIN_MAX_FAILURES, window: int = LOGIN_WINDOW_SECONDS):
        self._max = max_failures
        self._window = window
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < self._window]

    def is_blocked(self, *keys: str) -> bool:
        now = time.time()
        with self._lock:
            return any(len(self._recent(k, now)) >= self._max for k in keys)

    def record_failure(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            if len(self._failures) > LIMITER_MAX_KEYS:
                self._prune(now)
            for k in keys:
                self._failures[k] = [*self._recent(k, now), now]

    def _prune(self, now: float) -> None:
        alive = {k: recent for k in list(self._failures) if (recent := self._recent(k, now))}
        # si aun así hay demasiadas, se descartan las más antiguas
        newest = sorted(alive, key=lambda k: alive[k][-1], reverse=True)[:LIMITER_MAX_KEYS // 2]
        self._failures = defaultdict(list, {k: alive[k] for k in newest})

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)
