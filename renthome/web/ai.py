"""Cliente del modelo de lenguaje local (servidor compatible con la API de OpenAI).

Todo se procesa en el propio servidor: ningún dato sale a servicios externos de IA.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("renthome.ai")
DEFAULT_TIMEOUT_SECONDS = 120


_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_waiting_lock = threading.Lock()
_interactive_waiting = 0


class interactive:
    """Contexto para peticiones de un usuario: las tareas de fondo ceden el modelo mientras dura."""

    def __enter__(self) -> None:
        global _interactive_waiting
        with _waiting_lock:
            _interactive_waiting += 1

    def __exit__(self, *_exc) -> None:
        global _interactive_waiting
        with _waiting_lock:
            _interactive_waiting -= 1


def someone_is_waiting() -> bool:
    return _interactive_waiting > 0


def strip_links(text: str) -> str:
    """Quita enlaces de un texto generado por el modelo (un anuncio hostil podría colarlos)."""
    return _URL_RE.sub("[enlace eliminado]", text)


class LLMUnavailable(Exception):
    """El modelo no responde o devolvió algo que no se puede interpretar."""


@dataclass(frozen=True)
class LLM:
    base_url: str
    model: str
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "LLM | None":
        base = os.environ.get("RENTHOME_LLM_URL", "").rstrip("/")
        if not base:
            return None
        return cls(base_url=base, model=os.environ.get("RENTHOME_LLM_MODEL", "qwen3.8-27b"))

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise LLMUnavailable(f"el modelo local no responde: {exc}") from exc

    def _chat(self, system: str, user: str, max_tokens: int, temperature: float, as_json: bool) -> str:
        payload = {
            "model": self.model, "temperature": temperature, "max_tokens": max_tokens,
            # los modelos Qwen "piensan" por defecto y agotan los tokens antes de responder
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if as_json:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable("respuesta inesperada del modelo") from exc
        if not content or not content.strip():
            raise LLMUnavailable("el modelo devolvió una respuesta vacía")
        return content.strip()

    def chat_json(self, system: str, user: str, max_tokens: int = 500) -> dict:
        content = self._chat(system, user, max_tokens, temperature=0.0, as_json=True)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMUnavailable("el modelo no devolvió JSON válido") from exc
        if not isinstance(parsed, dict):
            raise LLMUnavailable("el modelo no devolvió un objeto JSON")
        return parsed

    def chat_text(self, system: str, user: str, max_tokens: int = 700, temperature: float = 0.4) -> str:
        return self._chat(system, user, max_tokens, temperature, as_json=False)


# ---------- lectura segura de las respuestas ----------

def as_int(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(float(str(value).replace(".", "").replace(",", ".")) if isinstance(value, str) else value)
    except ValueError:
        return None
    return number if minimum <= number <= maximum else None


def as_text(value: object, limit: int) -> str:
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if v)
    return " ".join(strip_links(str(value or "")).split())[:limit]


def as_list(value: object, limit: int, item_limit: int = 60) -> list[str]:
    if not isinstance(value, list):
        return []
    return [as_text(v, item_limit) for v in value if as_text(v, item_limit)][:limit]
