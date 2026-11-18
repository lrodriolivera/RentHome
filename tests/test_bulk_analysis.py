"""Analizar varias webs de inmobiliarias a la vez (pegadas por el administrador)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from renthome.web import discovery
from renthome.web.models import AgencyCandidate

from .conftest import add_agency, add_user, csrf, db_session, login
from .test_ai import FakeLLM


class RecordingWorker:
    def __init__(self, real):
        self.real, self.analyses = real, []

    def request_analysis(self, url: str, area: str, town: str = "") -> None:
        self.analyses.append((url, area, town))

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture()
def admin(app):
    add_user(app, "admin@example.org", role="admin")
    app.state.llm = FakeLLM()
    app.state.worker = RecordingWorker(app.state.worker)
    return login(app, "admin@example.org")


def paste(client, text: str, area: str = "ibiza", town: str = ""):
    return client.post("/admin/candidatas/analizar", data={
        "urls": text, "area": area, "town": town, "csrf_token": csrf(client, "/admin/candidatas")})


# ---------- lectura de la lista ----------

def test_parse_sites_accepts_lines_commas_and_full_urls():
    text = """https://www.inmo-uno.es/alquiler
    inmo-dos.com, www.inmo-tres.es ; https://inmo-uno.es/otra
    https://www.idealista.com/x
    no es una web
    http://192.168.1.10"""
    valid, rejected = discovery.parse_sites(text)
    assert valid == ["https://www.inmo-uno.es", "https://inmo-dos.com", "https://www.inmo-tres.es"]
    assert "https://www.idealista.com/x" in rejected and "http://192.168.1.10" in rejected
    assert "no" in rejected or "no es una web" in " ".join(rejected)


def test_parse_sites_caps_the_batch():
    valid, rejected = discovery.parse_sites("\n".join(f"inmo-{n}.es" for n in range(40)))
    assert len(valid) == discovery.MAX_BULK_SITES and rejected == []


# ---------- panel ----------

def test_pasted_sites_are_queued_and_listed_as_in_analysis(app, admin):
    page = paste(admin, "https://www.inmo-uno.es\ninmo-dos.com\nhttps://www.fotocasa.es", town="Sant Antoni de Portmany").text
    assert "Analizando 2 webs" in page and "fotocasa" in page
    assert app.state.worker.analyses == [("https://www.inmo-uno.es", "ibiza", "Sant Antoni de Portmany"),
                                         ("https://inmo-dos.com", "ibiza", "Sant Antoni de Portmany")]
    with db_session(app) as db:
        rows = db.scalars(select(AgencyCandidate).order_by(AgencyCandidate.id)).all()
        assert [(c.website, c.status, c.town) for c in rows] == [
            ("https://inmo-uno.es", "analysing", "Sant Antoni de Portmany"),
            ("https://inmo-dos.com", "analysing", "Sant Antoni de Portmany")]
    panel = admin.get("/admin/candidatas").text
    assert "En análisis (2)" in panel and "inmo-uno.es" in panel


def test_known_agencies_and_pending_candidates_are_skipped(app, admin):
    add_agency(app, website="https://www.ya-rastreada.es")
    with db_session(app) as db:
        db.add(AgencyCandidate(website="https://pendiente.es", name="Pendiente", area="ibiza", status="pending"))
        db.add(AgencyCandidate(website="https://descartada.es", name="Descartada", area="ibiza", status="discarded"))
        db.commit()
    page = paste(admin, "ya-rastreada.es\npendiente.es\ndescartada.es").text
    assert "Analizando 1 web" in page and "ya conocidas: 2" in page
    assert [a[0] for a in app.state.worker.analyses] == ["https://descartada.es"]  # se puede reintentar
    with db_session(app) as db:
        assert db.scalar(select(AgencyCandidate).where(AgencyCandidate.website == "https://descartada.es")).status == "analysing"


def test_single_url_field_still_works(app, admin):
    admin.post("/admin/candidatas/analizar", data={"url": "https://www.inmo-sola.es", "area": "menorca",
                                                   "csrf_token": csrf(admin, "/admin/candidatas")})
    assert app.state.worker.analyses == [("https://www.inmo-sola.es", "menorca", "")]


def test_nothing_valid_shows_an_error(app, admin):
    page = paste(admin, "https://www.idealista.com\nhttp://localhost:8080").text
    assert "Analizando" not in page and "Ninguna" in page
    assert app.state.worker.analyses == []


def test_bulk_needs_area_and_valid_town(app, admin):
    assert "Elige la zona" in paste(admin, "inmo-uno.es", area="").text
    assert "Analizando" not in paste(admin, "inmo-uno.es", town="https://evil.test").text
    assert app.state.worker.analyses == []


def test_bulk_is_admin_only(app, admin):
    add_user(app, "ana@example.org")
    user = login(app, "ana@example.org")
    response = user.post("/admin/candidatas/analizar", data={"urls": "inmo-uno.es", "area": "ibiza",
                                                             "csrf_token": csrf(user, "/perfil")})
    assert response.status_code == 404 and app.state.worker.analyses == []


# ---------- worker ----------

def test_worker_requeues_analyses_interrupted_by_a_restart(app):
    with db_session(app) as db:
        db.add(AgencyCandidate(website="https://a-medias.es", name="a-medias.es", area="mallorca", town="Sóller",
                               status="analysing"))
        db.commit()
    worker = app.state.worker
    queued = []
    worker._queue.put = lambda item: queued.append(item)
    worker._thread_start = False
    worker.requeue_pending_analyses()
    assert queued == [("analyse", ("https://a-medias.es", "mallorca", "Sóller"))]


def test_analysis_result_keeps_the_town(app, monkeypatch):
    def analyse(_llm, _fetcher, url, area):
        return discovery.Proposal({"name": "Inmo", "areas": [area], "website": url}, "local", "6 fichas", True)

    monkeypatch.setattr(discovery, "analyse_site", analyse)
    worker = app.state.worker
    worker.llm = FakeLLM()
    worker._analyse(("https://inmo.es", "ibiza", "Sant Josep"), fetcher=None)
    with db_session(app) as db:
        candidate = db.scalar(select(AgencyCandidate))
        assert (candidate.status, candidate.town) == ("pending", "Sant Josep")
