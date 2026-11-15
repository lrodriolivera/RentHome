"""Recorre las páginas de listado de cada inmobiliaria y extrae cada ficha."""
from __future__ import annotations

import logging
import re
from collections import deque
from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup

from .fetch import Fetcher
from .parse import extract

log = logging.getLogger(__name__)


def _links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "whatsapp:")):
            continue
        out.append(urldefrag(urljoin(base, href))[0])
    return out


def discover(fetcher: Fetcher, src: dict) -> list[str]:
    """Devuelve las URLs de fichas encontradas en las páginas de listado."""
    detail_rx = re.compile(src["detail_pattern"])
    page_rx = re.compile(src["page_pattern"]) if src.get("page_pattern") else None
    max_pages = src.get("max_pages", 3)
    render = src.get("render", False)

    queue = deque(src["start_urls"])
    seen_pages: set[str] = set()
    details: dict[str, None] = {}
    while queue and len(seen_pages) < max_pages * max(1, len(src["start_urls"])):
        url = queue.popleft()
        if url in seen_pages:
            continue
        seen_pages.add(url)
        html = fetcher.get(url, render=render)
        if html is None:
            continue
        links = _links(html, url)
        found = [l for l in links if detail_rx.search(l)]
        # web con JS: reintentar con navegador si no salió nada
        if not found and not render and fetcher.render_available():
            log.info("  sin enlaces en %s, probando con navegador", url)
            html2 = fetcher.render(url)
            if html2:
                links = _links(html2, url)
                found = [l for l in links if detail_rx.search(l)]
        for l in found:
            details.setdefault(l.rstrip("/") + ("/" if l.endswith("/") else ""), None)
        if page_rx:
            for l in links:
                if page_rx.search(l) and l not in seen_pages:
                    queue.append(l)
        log.info("  listado %s -> %d fichas", url, len(found))
    return list(details)


def crawl_source(fetcher: Fetcher, src: dict, max_details: int) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    try:
        urls = discover(fetcher, src)
    except Exception as e:  # noqa: BLE001
        log.exception("Error recorriendo %s", src["name"])
        return [], [f"{src['name']}: {e}"]
    if not urls:
        errors.append(f"{src['name']}: no se encontraron fichas (¿cambió la web?)")
    results = []
    for url in urls[:max_details]:
        html = fetcher.get(url, render=src.get("render_details", False))
        if not html:
            continue
        try:
            item = extract(html, url, src["name"], src.get("selectors")).to_dict()
        except Exception as e:  # noqa: BLE001
            log.warning("  error extrayendo %s: %s", url, e)
            continue
        results.append(item)
    log.info("%s: %d fichas analizadas", src["name"], len(results))
    return results, errors
