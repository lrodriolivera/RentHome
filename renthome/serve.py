"""Modo servidor para homelab: sirve la web y lanza la búsqueda semanal.

    python -m renthome.serve

Variables de entorno:
    PORT               puerto de la web (8080)
    RENTHOME_SCHEDULE  día y hora de la búsqueda, p. ej. "mon 07:47" (vacío = sin programar)
    TZ                 zona horaria (Europe/Madrid)
    RENTHOME_COOLDOWN_MIN  minutos mínimos entre búsquedas manuales (30)
    NTFY_URL / SMTP_*  avisos de anuncios nuevos (ver renthome/notify.py)

Rutas:
    /             listado (docs/index.html)
    GET  /api/status   estado de la búsqueda
    POST /api/run      lanzar una búsqueda ahora
"""
from __future__ import annotations

import datetime as dt
import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
log = logging.getLogger("renthome.serve")

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.last_start: str | None = None
        self.last_end: str | None = None
        self.last_ok: bool | None = None
        self._last_start_ts = 0.0

    def status(self) -> dict:
        last = {}
        f = DATA / "last_run.json"
        if f.exists():
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
                last = {"date": s.get("date"), "matching": s.get("matching"),
                        "new": len(s.get("new", [])), "scanned": s.get("scanned")}
            except Exception:  # noqa: BLE001
                pass
        return {"running": self.running, "last_start": self.last_start, "last_end": self.last_end,
                "last_ok": self.last_ok, "last_run": last, "next_run": next_run_text()}

    def start(self, reason: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self._last_start_ts = time.time()
            self.last_start = dt.datetime.now(tz()).isoformat(timespec="minutes")
        threading.Thread(target=self._run, args=(reason,), daemon=True).start()
        return True

    def seconds_since_start(self) -> float:
        return time.time() - self._last_start_ts

    def _run(self, reason: str):
        log.info("Búsqueda iniciada (%s)", reason)
        ok = False
        try:
            with open(DATA / "last_crawl.log", "w", encoding="utf-8") as out:
                r = subprocess.run([sys.executable, "-m", "renthome"], cwd=ROOT, stdout=out,
                                   stderr=subprocess.STDOUT, timeout=3 * 3600)
            ok = r.returncode == 0
            if ok:
                subprocess.run([sys.executable, "-m", "renthome.notify"], cwd=ROOT, timeout=120)
        except Exception as e:  # noqa: BLE001
            log.exception("La búsqueda falló: %s", e)
        finally:
            self.last_ok = ok
            self.last_end = dt.datetime.now(tz()).isoformat(timespec="minutes")
            self.running = False
            log.info("Búsqueda terminada (ok=%s)", ok)


RUNNER = Runner()


def tz():
    return ZoneInfo(os.environ.get("TZ") or "Europe/Madrid")


def parse_schedule():
    raw = (os.environ.get("RENTHOME_SCHEDULE") or "").strip().lower()
    if not raw:
        return None
    day, hm = raw.split()
    h, m = hm.split(":")
    return DAYS[day[:3]], int(h), int(m)


def next_run_text() -> str | None:
    sch = parse_schedule()
    if not sch:
        return None
    wd, h, m = sch
    now = dt.datetime.now(tz())
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0) + dt.timedelta(days=(wd - now.weekday()) % 7)
    if cand <= now:
        cand += dt.timedelta(days=7)
    return cand.isoformat(timespec="minutes")


def scheduler():
    sch = parse_schedule()
    if not sch:
        log.info("Sin RENTHOME_SCHEDULE: la búsqueda solo se lanza a mano")
        return
    wd, h, m = sch
    done_for = None
    log.info("Búsqueda programada: %s (próxima %s)", os.environ.get("RENTHOME_SCHEDULE"), next_run_text())
    while True:
        now = dt.datetime.now(tz())
        if now.weekday() == wd and (now.hour, now.minute) >= (h, m) and done_for != now.date():
            done_for = now.date()
            RUNNER.start("programada")
        time.sleep(30)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DOCS), **k)

    def log_message(self, fmt, *args):  # menos ruido
        log.debug(fmt, *args)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _can_run(self) -> bool:
        """Lanzar búsquedas a mano solo desde la red local / Tailscale.

        Cloudflare añade siempre la cabecera Cf-Connecting-Ip a lo que pasa por el túnel;
        con RENTHOME_PUBLIC_READONLY=1 (por defecto) esas peticiones son de solo lectura.
        """
        if os.environ.get("RENTHOME_PUBLIC_READONLY", "1") == "0":
            return True
        return self.headers.get("Cf-Connecting-Ip") is None

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json(200, {**RUNNER.status(), "can_run": self._can_run()})
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            body = (DOCS / "index.html").read_bytes()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/run"):
            return self._json(404, {"error": "no encontrado"})
        if not self._can_run():
            return self._json(403, {"error": "Solo lectura desde Internet: lanza la búsqueda desde tu red o Tailscale"})
        cooldown = 60 * int(os.environ.get("RENTHOME_COOLDOWN_MIN") or 30)
        if RUNNER.running:
            return self._json(409, {"error": "Ya hay una búsqueda en marcha"})
        if RUNNER.seconds_since_start() < cooldown:
            mins = int((cooldown - RUNNER.seconds_since_start()) // 60) + 1
            return self._json(429, {"error": f"Espera {mins} min antes de volver a buscar"})
        RUNNER.start("manual")
        return self._json(202, {**RUNNER.status(), "can_run": True})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    DATA.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    if not (DOCS / "index.html").exists():
        subprocess.run([sys.executable, "-m", "renthome", "--report-only"], cwd=ROOT)
    threading.Thread(target=scheduler, daemon=True).start()
    port = int(os.environ.get("PORT") or 8080)
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("RentHome en http://0.0.0.0:%d", port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
