"""Búsqueda de inmobiliarias por ciudad o municipio (panel de administración)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from renthome.web import agency_finder, discovery
from renthome.web.models import AgencyCandidate, TownSearch

from .conftest import add_agency, add_user, csrf, db_session, login
from .test_ai import FakeLLM


class RecordingWorker:
    """Sustituye al worker real: solo apunta lo que se le pide."""

    def __init__(self, real):
        self.real, self.towns = real, []

    def request_town_discovery(self, search_id: int) -> None:
        self.towns.append(search_id)

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture()
def admin(app):
    add_user(app, "admin@example.org", role="admin")
    app.state.llm = FakeLLM()
    app.state.worker = RecordingWorker(app.state.worker)
    return login(app, "admin@example.org")


def search_town(client, town: str = "Santa Eulària des Riu", area: str = "ibiza"):
    return client.post("/admin/candidatas/ciudad",
                       data={"town": town, "area": area, "csrf_token": csrf(client, "/admin/candidatas")})


class FakeFinder:
    def __init__(self, sites: list[str], notes: str = ""):
        self.sites, self.notes, self.calls = sites, notes, []

    def find(self, area: str, town: str) -> agency_finder.FoundSites:
        self.calls.append((area, town))
        return agency_finder.FoundSites(sites=list(dict.fromkeys(self.sites)), osm=len(self.sites), web=0,
                                        notes=self.notes)


def fake_proposal(viable: bool):
    def analyse(_llm, _fetcher, url, area):
        entry = {"name": f"Agencia {discovery.site_key(url)}", "areas": [area], "website": url}
        return discovery.Proposal(entry, "local", "5 fichas" if viable else "sin fichas", viable)
    return analyse


# ---------- utilidades ----------

@pytest.mark.parametrize("raw, expected", [("  sant  cugat del vallès ", "Sant Cugat del Vallès"),
                                           ("L'Hospitalet de Llobregat", "L'Hospitalet de Llobregat"),
                                           ("maó-mahón", "Maó-Mahón")])
def test_clean_town_normalises_names(raw, expected):
    assert discovery.clean_town(raw) == expected


@pytest.mark.parametrize("raw", ["", "a", "Ibiza; DROP TABLE", "https://x.com", "x" * 61, "12345"])
def test_clean_town_rejects_invalid_names(raw):
    with pytest.raises(ValueError):
        discovery.clean_town(raw)


def test_run_town_analyses_only_new_sites_and_records_town(app, monkeypatch):
    add_agency(app, website="https://ya-conocida.es")
    finder = FakeFinder(["https://ya-conocida.es", "https://nueva-uno.es", "https://nueva-dos.es"], notes="nota")
    monkeypatch.setattr(discovery, "analyse_site", fake_proposal(viable=True))
    result = discovery.run_town(app.state.session_factory, FakeLLM(), None, "ibiza", "Sant Josep", finder)
    assert (result.suggested, result.analysed, result.viable) == (3, 2, 2)
    assert finder.calls == [("ibiza", "Sant Josep")] and "OpenStreetMap: 3" in result.sources and "nota" in result.sources
    with db_session(app) as db:
        rows = db.scalars(select(AgencyCandidate).order_by(AgencyCandidate.website)).all()
        assert [c.website for c in rows] == ["https://nueva-dos.es", "https://nueva-uno.es"]
        assert {c.town for c in rows} == {"Sant Josep"} and {c.status for c in rows} == {"pending"}


def test_run_town_respects_limit(app, monkeypatch):
    finder = FakeFinder([f"https://agencia-{n}.es" for n in range(20)])
    monkeypatch.setattr(discovery, "analyse_site", fake_proposal(viable=False))
    result = discovery.run_town(app.state.session_factory, FakeLLM(), None, "mallorca", "Sóller", finder, limit=4)
    assert (result.suggested, result.analysed, result.viable) == (20, 4, 0)


# ---------- worker ----------

def test_worker_runs_town_search_and_stores_counts(app, monkeypatch):
    with db_session(app) as db:
        search = TownSearch(area="ibiza", town="Sant Antoni de Portmany")
        db.add(search)
        db.commit()
        sid = search.id
    calls = []

    def fake_run(factory, llm, fetcher, area, town, finder, limit=discovery.TOWN_LIMIT):
        calls.append((area, town))
        return discovery.TownResult(suggested=7, analysed=5, viable=2, sources="OpenStreetMap: 3 · web: 4")

    monkeypatch.setattr(discovery, "run_town", fake_run)
    app.state.worker.llm = FakeLLM()
    app.state.worker._discover_town(sid, fetcher=None)
    assert calls == [("ibiza", "Sant Antoni de Portmany")]
    with db_session(app) as db:
        done = db.get(TownSearch, sid)
        assert (done.status, done.suggested, done.analysed, done.viable) == ("done", 7, 5, 2)
        assert done.sources == "OpenStreetMap: 3 · web: 4"
        assert done.finished_at is not None


def test_weekly_discovery_uses_real_sources_not_ai_suggestions(app, monkeypatch):
    finder = FakeFinder(["https://real-uno.es"])
    monkeypatch.setattr(discovery, "analyse_site", fake_proposal(viable=True))
    llm = FakeLLM()
    assert discovery.run(app.state.session_factory, llm, None, ["menorca"], finder) == 1
    assert finder.calls == [("menorca", "")]
    assert not hasattr(discovery, "suggest_sites") and not hasattr(discovery, "SUGGEST_SYSTEM")


def test_worker_marks_town_search_failed_when_ai_is_down(app, monkeypatch):
    with db_session(app) as db:
        search = TownSearch(area="ibiza", town="Eivissa")
        db.add(search)
        db.commit()
        sid = search.id

    def broken(*_a, **_k):
        raise discovery.LLMUnavailable("caído")

    monkeypatch.setattr(discovery, "run_town", broken)
    app.state.worker.llm = FakeLLM()
    app.state.worker._discover_town(sid, fetcher=None)
    with db_session(app) as db:
        failed = db.get(TownSearch, sid)
        assert failed.status == "failed" and "IA" in failed.error


# ---------- panel ----------

def test_admin_queues_town_search_and_sees_it_listed(app, admin):
    response = search_town(admin)
    assert response.status_code == 200 and "Buscando inmobiliarias en Santa Eulària des Riu" in response.text
    with db_session(app) as db:
        search = db.scalar(select(TownSearch))
        assert (search.area, search.town, search.status) == ("ibiza", "Santa Eulària des Riu", "queued")
    assert app.state.worker.towns == [search.id]
    assert "Santa Eulària des Riu" in admin.get("/admin/candidatas").text


def test_town_search_rejects_bad_input_and_duplicates(app, admin):
    assert "Buscando" not in search_town(admin, town="https://evil.test").text
    assert "Buscando" not in search_town(admin, area="madrid").text
    search_town(admin, town="Sóller", area="mallorca")
    again = search_town(admin, town="sóller", area="mallorca")
    assert "ya se está buscando" in again.text
    with db_session(app) as db:
        assert len(db.scalars(select(TownSearch)).all()) == 1
    assert len(app.state.worker.towns) == 1


def test_town_search_needs_ai(app, admin):
    app.state.llm = None
    assert "IA local" in search_town(admin).text
    assert app.state.worker.towns == []


def test_town_search_is_admin_only(app, admin):
    add_user(app, "ana@example.org")
    user = login(app, "ana@example.org")
    response = user.post("/admin/candidatas/ciudad",
                         data={"town": "Eivissa", "area": "ibiza", "csrf_token": csrf(user, "/perfil")})
    assert response.status_code == 404
    assert app.state.worker.towns == []


def test_candidate_shows_its_town(app, admin):
    with db_session(app) as db:
        db.add(AgencyCandidate(website="https://inmo-soller.es", name="Inmo Sóller", area="mallorca",
                               town="Sóller", probe_result="6 fichas", status="pending"))
        db.commit()
    page = admin.get("/admin/candidatas").text
    assert "Inmo Sóller" in page and "Sóller" in page
