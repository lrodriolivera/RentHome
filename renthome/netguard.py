"""Protección frente a SSRF: el rastreador solo debe visitar webs públicas.

El servidor vive en una red con otros servicios (Docker, Tailscale). Una web hostil podría
redirigir o enlazar a direcciones internas para que el rastreador las lea; aquí se impide.
"""
from __future__ import annotations

import re

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")
INTERNAL_SUFFIXES = (".local", ".internal", ".lan", ".localhost", ".home", ".corp", ".svc", ".ts.net")
MAX_URL_LENGTH = 500
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # incluye las direcciones de Tailscale


def host_key(url: str) -> str:
    """Nombre de host en minúsculas y sin «www.», para comparar sitios."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and ip not in _CGNAT if isinstance(ip, ipaddress.IPv4Address) else ip.is_global


def looks_public(url: str) -> bool:
    """Comprobación sin red: esquema web, sin usuario ni IP literal, y nombre de dominio normal."""
    if len(url) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ALLOWED_SCHEMES or parsed.username or parsed.password or not host:
            return False
        if parsed.port not in (None, 80, 443):
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # las inmobiliarias tienen dominio; una IP literal no es una de ellas
    except ValueError:
        pass
    return "." in host and host != "localhost" and not host.endswith(INTERNAL_SUFFIXES)


@lru_cache(maxsize=2048)
def _host_resolves_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(_ip_is_public(ipaddress.ip_address(a.split("%")[0])) for a in addresses)


def is_public_url(url: str) -> bool:
    """`looks_public` y además el dominio resuelve únicamente a direcciones públicas."""
    return looks_public(url) and _host_resolves_public((urlparse(url).hostname or "").lower())


_HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def normalise_site(url: str) -> str | None:
    """«https://dominio» de una web pública, o None si no lo parece. Descarta usuario, puerto y ruta."""
    candidate = url.strip() if "//" in url else f"https://{url.strip()}"
    if not looks_public(candidate):
        return None
    host = (urlparse(candidate).hostname or "").lower()
    if not _HOSTNAME.fullmatch(host):  # p. ej. «inmobiliariadeibiza oeste.com» o sin dominio
        return None
    return f"https://{host}"
