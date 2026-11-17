"""Límite de tamaño del cuerpo de las peticiones (ASGI puro, antes de leer nada)."""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import MAX_FORM_REQUEST_BYTES, MAX_UPLOAD_REQUEST_BYTES

UPLOAD_PATHS = ("/documentos",)


class BodyTooLarge(Exception):
    pass


def limit_for(path: str) -> int:
    return MAX_UPLOAD_REQUEST_BYTES if path in UPLOAD_PATHS else MAX_FORM_REQUEST_BYTES


class BodyLimitMiddleware:
    """Rechaza con 413 los cuerpos demasiado grandes, declarados o enviados por trozos."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await self._reject(send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = "La petición es demasiado grande.".encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
