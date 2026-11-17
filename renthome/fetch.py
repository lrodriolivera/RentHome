"""Descarga de páginas con cortesía (pausa por web, reintentos) y renderizado
opcional con Chromium (Playwright) para webs que cargan anuncios con JS."""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlparse

import requests

MAX_REDIRECTS = 5

log = logging.getLogger(__name__)


class Fetcher:
    def __init__(self, user_agent: str, delay: float = 1.0, timeout: int = 30, url_guard=None):
        # url_guard(url) -> bool: si se indica, solo se visitan las URLs que acepta
        # (también en cada salto de una redirección y en lo que cargue el navegador)
        self.url_guard = url_guard
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
        self._last: dict[str, float] = {}
        self._pw = None
        self._browser = None

    def _wait(self, url: str):
        host = urlparse(url).netloc
        last = self._last.get(host, 0)
        gap = time.time() - last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last[host] = time.time()

    def get(self, url: str, render: bool = False) -> str | None:
        if render:
            return self.render(url)
        for attempt in range(3):
            self._wait(url)
            try:
                r = self._get_following_redirects(url)
                if r is None:
                    log.warning("  %s -> destino no permitido", url)
                    return None
                if r.status_code == 200:
                    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
                    return r.text
                if r.status_code in (404, 410):
                    log.info("  %s -> %s", url, r.status_code)
                    return None
                log.warning("  %s -> HTTP %s (intento %d)", url, r.status_code, attempt + 1)
            except requests.RequestException as e:
                log.warning("  %s -> %s (intento %d)", url, e.__class__.__name__, attempt + 1)
            time.sleep(2 * (attempt + 1))
        return None

    def _get_following_redirects(self, url: str):
        """GET siguiendo redirecciones a mano para validar cada salto. None si alguno no se permite."""
        if self.url_guard is None:
            return self.session.get(url, timeout=self.timeout)
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not self.url_guard(current):
                return None
            r = self.session.get(current, timeout=self.timeout, allow_redirects=False)
            if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("Location"):
                return r
            current = requests.compat.urljoin(current, r.headers["Location"])
        return None

    def _guard_route(self, route) -> None:
        if self.url_guard(route.request.url):
            route.continue_()
        else:
            route.abort()

    # ---------------------------------------------------------------- render
    def render_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def render(self, url: str) -> str | None:
        if not self.render_available():
            log.warning("  Playwright no instalado: no se puede renderizar %s", url)
            return None
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                kw = {}
                exe = os.environ.get("CHROMIUM_PATH")
                if exe:
                    kw["executable_path"] = exe
                self._browser = self._pw.chromium.launch(**kw)
            if self.url_guard is not None and not self.url_guard(url):
                log.warning("  %s -> destino no permitido", url)
                return None
            self._wait(url)
            page = self._browser.new_page(user_agent=self.session.headers["User-Agent"])
            if self.url_guard is not None:
                page.route("**/*", self._guard_route)
            try:
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 2000)
                page.wait_for_timeout(2500)
                # desplazar para cargar listados perezosos
                for _ in range(4):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(600)
                return page.content()
            finally:
                page.close()
        except Exception as e:  # noqa: BLE001
            log.warning("  render %s falló: %s", url, e)
            return None

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
