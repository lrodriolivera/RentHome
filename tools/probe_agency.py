"""Comprueba que una inmobiliaria se puede rastrear con el extractor genérico.

    python tools/probe_agency.py candidatas.yaml [--details 3]

El YAML es una lista de fuentes con el mismo formato que `sources` en config.yaml
(name, website, start_urls, detail_pattern, page_pattern, max_pages, render, long_term_section).
Imprime cuántas fichas descubre cada una y qué extrae de las primeras.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renthome.crawl import discover  # noqa: E402
from renthome.fetch import Fetcher  # noqa: E402
from renthome.parse import extract  # noqa: E402

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 RentHomeIbiza/1.0")


def probe(fetcher: Fetcher, src: dict, details: int) -> None:
    out = sys.stdout.write
    out(f"\n=== {src['name']} ({src.get('website', '')})\n")
    urls, badges = discover(fetcher, {**src, "max_pages": min(src.get("max_pages", 2), 2)})
    out(f"fichas descubiertas: {len(urls)} | con etiqueta alquilado/reservado: {len(badges)}\n")
    for url in urls[:details]:
        html = fetcher.get(url, render=src.get("render_details", False))
        if not html:
            out(f"  ! no se pudo leer {url}\n")
            continue
        item = extract(html, url, src["name"], src.get("selectors")).to_dict()
        out(f"  - {item['title'][:70]!r}\n    precio={item['price']} anual={item['annual_price']} hab={item['bedrooms']} "
            f"tipo_alquiler={item['term']} estado={item['status']} operacion={item['operation']}\n    {url}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--details", type=int, default=3)
    args = parser.parse_args()
    sources = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    fetcher = Fetcher(USER_AGENT, delay=1.0, timeout=25)
    try:
        for src in sources:
            try:
                probe(fetcher, src, args.details)
            except Exception as exc:  # una candidata rota no detiene las demás
                sys.stdout.write(f"  ! error: {exc}\n")
    finally:
        fetcher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
