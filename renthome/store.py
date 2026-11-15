"""Historial de anuncios en data/listings.json para detectar novedades."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def listing_id(url: str) -> str:
    return hashlib.sha1(url.rstrip("/").encode()).hexdigest()[:12]


class Store:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"runs": [], "listings": {}}

    @property
    def listings(self) -> dict:
        return self.data["listings"]

    def update(self, items: list[dict], run_date: str, scanned_sources: set[str]) -> dict:
        """Fusiona los resultados de una ejecución. Devuelve resumen de cambios."""
        new, price_drops, back = [], [], []
        seen_ids = set()
        for it in items:
            lid = listing_id(it["url"])
            seen_ids.add(lid)
            old = self.listings.get(lid)
            rec = dict(it)
            rec["id"] = lid
            if old is None:
                rec["first_seen"] = run_date
                rec["first_matched"] = run_date if it["matches"] else None
                rec["price_history"] = [[run_date, it.get("price")]] if it.get("price") else []
                if it["matches"]:
                    new.append(lid)
            else:
                rec["first_seen"] = old.get("first_seen", run_date)
                rec["first_matched"] = old.get("first_matched")
                rec["price_history"] = old.get("price_history", [])
                if it.get("price") and (not rec["price_history"] or rec["price_history"][-1][1] != it["price"]):
                    prev = rec["price_history"][-1][1] if rec["price_history"] else None
                    rec["price_history"].append([run_date, it["price"]])
                    if it["matches"] and prev and it["price"] < prev:
                        price_drops.append(lid)
                if it["matches"] and not rec["first_matched"]:
                    rec["first_matched"] = run_date
                    new.append(lid)
                elif it["matches"] and not old.get("active", True):
                    back.append(lid)
            rec["last_seen"] = run_date
            rec["active"] = True
            self.listings[lid] = rec

        # anuncios que ya no aparecen (solo en fuentes que sí se pudieron leer)
        gone = []
        for lid, rec in self.listings.items():
            if lid not in seen_ids and rec.get("source") in scanned_sources and rec.get("active"):
                rec["active"] = False
                if rec.get("matches"):
                    gone.append(lid)

        summary = {
            "date": run_date,
            "scanned": len(items),
            "matching": sum(1 for i in items if i["matches"]),
            "new": new,
            "price_drops": price_drops,
            "back": back,
            "gone": gone,
        }
        self.data["runs"].append({k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()})
        self.data["runs"] = self.data["runs"][-104:]
        return summary

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
