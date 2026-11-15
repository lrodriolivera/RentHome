"""RentHome Ibiza – buscador semanal de alquiler anual en inmobiliarias locales.

Uso:
    python -m renthome                 # rastrea todas las webs y genera informes
    python -m renthome --only "Living in Ibiza"
    python -m renthome --report-only   # regenera informes desde data/listings.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import yaml

from .crawl import crawl_source
from .fetch import Fetcher
from .filters import evaluate
from .report import write_reports
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="renthome")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--data", default=str(ROOT / "data" / "listings.json"))
    ap.add_argument("--only", action="append", help="nombre de inmobiliaria (repetible)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--date", help="fecha de la ejecución (AAAA-MM-DD)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("renthome")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    criteria = cfg["criteria"]
    crawler = cfg.get("crawler", {})
    sources = cfg["sources"]
    if args.only:
        wanted = {o.lower() for o in args.only}
        sources = [s for s in sources if s["name"].lower() in wanted]

    store = Store(Path(args.data))
    run_date = args.date or dt.date.today().isoformat()
    by_name = {s["name"]: s for s in cfg["sources"]}

    if args.report_only:
        summary = {"date": run_date, "new": [], "price_drops": [], "back": [], "gone": [], "errors": []}
        last = Path(ROOT / "data" / "last_run.json")
        if last.exists():
            summary = json.loads(last.read_text(encoding="utf-8"))
        write_reports(store, criteria, cfg["sources"], summary, ROOT)
        return 0

    fetcher = Fetcher(crawler.get("user_agent", "RentHomeIbiza/1.0"),
                      delay=crawler.get("delay_seconds", 1.0),
                      timeout=crawler.get("timeout_seconds", 30))
    all_items: list[dict] = []
    errors: list[str] = []
    scanned: set[str] = set()
    try:
        for src in sources:
            log.info("== %s", src["name"])
            items, errs = crawl_source(fetcher, src, crawler.get("max_detail_pages_per_source", 150))
            errors += errs
            if items:
                scanned.add(src["name"])
            for it in items:
                ok, notes, score = evaluate(it, criteria, src.get("long_term_section", False))
                it["matches"] = ok
                it["notes"] = notes
                it["score"] = score
                it["agency_web"] = src.get("website")
            all_items += items
    finally:
        fetcher.close()

    # el mismo inmueble puede salir en varias categorías de una web
    dedup = {}
    for it in all_items:
        dedup[it["url"].rstrip("/")] = it
    all_items = list(dedup.values())

    summary = store.update(all_items, run_date, scanned)
    summary["errors"] = errors
    summary["sources_ok"] = sorted(scanned)
    summary["sources_total"] = len(sources)
    store.save()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    write_reports(store, criteria, cfg["sources"], summary, ROOT)

    log.info("Analizados %d anuncios, %d cumplen criterios, %d nuevos",
             summary["scanned"], summary["matching"], len(summary["new"]))
    for e in errors:
        log.warning(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
