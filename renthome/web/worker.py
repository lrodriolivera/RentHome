"""Cola de rastreos: un solo hilo, una inmobiliaria cada vez."""
from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import threading
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..fetch import Fetcher
from .crawl_service import CrawlResult, crawl_agency
from .models import Agency, CrawlJob, utcnow

log = logging.getLogger("renthome.worker")
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 RentHome/2.0 (buscador privado de alquiler)")
USER_REFRESH_HOURS = 3       # "Buscar ahora" no vuelve a rastrear lo que se leyó hace menos de esto
AUTO_REFRESH_HOURS = 24      # rastreo automático diario
SCHEDULER_INTERVAL_SECONDS = 30 * 60
MAX_CONSECUTIVE_FAILURES = 5  # tras tantos fallos seguidos, el rastreo automático la deja en paz


def stale_agencies(db: Session, areas: list[str] | None, max_age_hours: float, include_failing: bool) -> list[Agency]:
    cutoff = utcnow() - dt.timedelta(hours=max_age_hours)
    agencies = db.scalars(select(Agency).where(Agency.enabled)).all()
    return [a for a in agencies
            if (areas is None or set(a.area_list) & set(areas))
            and (a.last_crawled_at is None or a.last_crawled_at < cutoff)
            and (include_failing or a.consecutive_failures < MAX_CONSECUTIVE_FAILURES)]


def pending_agency_ids(db: Session) -> set[int]:
    return set(db.scalars(select(CrawlJob.agency_id).where(CrawlJob.status.in_(("queued", "running")))))


class CrawlWorker:
    def __init__(self, factory: sessionmaker[Session],
                 on_new_listings: Callable[[list[int]], None] | None = None) -> None:
        self._factory = factory
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_new = on_new_listings

    # ---------- encolar ----------
    def enqueue(self, db: Session, agency_ids: list[int], reason: str) -> int:
        """Crea trabajos para las inmobiliarias que no tengan ya uno pendiente. Devuelve cuántos."""
        busy = pending_agency_ids(db)
        jobs = [CrawlJob(agency_id=aid, reason=reason) for aid in agency_ids if aid not in busy]
        db.add_all(jobs)
        db.flush()
        job_ids = [job.id for job in jobs]
        db.commit()
        for job_id in job_ids:
            self._queue.put(job_id)
        return len(job_ids)

    def enqueue_stale(self, db: Session, areas: list[str] | None, max_age_hours: float, reason: str) -> int:
        include_failing = reason != "scheduled"
        agencies = stale_agencies(db, areas, max_age_hours, include_failing)
        return self.enqueue(db, [a.id for a in agencies], reason)

    # ---------- ciclo de vida ----------
    def start(self) -> None:
        with self._factory() as db:
            for job in db.scalars(select(CrawlJob).where(CrawlJob.status == "running")):
                job.status, job.error, job.finished_at = "failed", "interrumpido por un reinicio", utcnow()
            queued = [j.id for j in db.scalars(select(CrawlJob).where(CrawlJob.status == "queued"))]
            db.commit()
        for job_id in queued:
            self._queue.put(job_id)
        self._thread = threading.Thread(target=self._run, name="crawl-worker", daemon=True)
        self._thread.start()
        threading.Thread(target=self._schedule, name="crawl-scheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _schedule(self) -> None:
        while not self._stop.wait(5):
            try:
                with self._factory() as db:
                    queued = self.enqueue_stale(db, None, AUTO_REFRESH_HOURS, "scheduled")
                if queued:
                    log.info("Rastreo automático: %d inmobiliarias en cola", queued)
            except Exception:
                log.exception("Fallo en el planificador de rastreos")
            if self._stop.wait(SCHEDULER_INTERVAL_SECONDS):
                return

    def _run(self) -> None:
        fetcher: Fetcher | None = None
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=20)
            except queue.Empty:
                if fetcher is not None:  # cola vacía: se cierra el navegador hasta la próxima tanda
                    fetcher.close()
                    fetcher = None
                continue
            if fetcher is None:
                fetcher = Fetcher(USER_AGENT, delay=float(os.environ.get("RENTHOME_CRAWL_DELAY", "1.0")), timeout=25)
            self._process(job_id, fetcher)

    def _process(self, job_id: int, fetcher: Fetcher) -> None:
        with self._factory() as db:
            job = db.get(CrawlJob, job_id)
            if job is None or job.status != "queued":
                return
            job.status, job.started_at = "running", utcnow()
            agency_id = job.agency_id
            db.commit()
        try:
            result = crawl_agency(self._factory, agency_id, fetcher)
        except Exception as exc:
            log.exception("Rastreo fallido (inmobiliaria %s)", agency_id)
            result = CrawlResult(error=f"error interno: {exc}"[:200])
        with self._factory() as db:
            job = db.get(CrawlJob, job_id)
            if job is not None:
                job.status = "failed" if result.error else "done"
                job.finished_at, job.found, job.new_count, job.error = utcnow(), result.found, result.new, result.error
                db.commit()
        if result.new_listing_ids and self._on_new is not None:
            try:
                self._on_new(result.new_listing_ids)
            except Exception:
                log.exception("Fallo al procesar anuncios nuevos")
