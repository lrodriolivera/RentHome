"""Servicios deseados cerca del piso: criterio opcional de búsqueda basado en los datos de la zona."""
from __future__ import annotations

import json

from sqlalchemy import select

from renthome.web import area_report, area_sources, catalog, criteria, matching
from renthome.web.models import AreaReport, SavedSearch, SearchPreference, User

from .conftest import add_agency, add_listing, add_user, csrf, db_session, login
from .test_area_report import FakeLLM, full_http, listing_in_cala_llonga


def pois(*present: str) -> dict:
    return {key: {"label": label, "count": 1 if key in present else 0, "nearest": []}
            for key, (label, _) in area_sources.CATEGORIES.items()}


def add_zone(app, listing_id: int, *present: str, status: str = "done") -> None:
    with db_session(app) as db:
        report = AreaReport(listing_id=listing_id, status=status,
                            facts_json=json.dumps({"pois": pois(*present)}),
                            services=area_report.services_from(pois(*present)))
        db.add(report)
        db.commit()


def user_with(app, **pref) -> tuple[int, User]:
    uid = add_user(app, "ana@example.org", areas="ibiza", **pref)
    return uid, uid


def find(app, uid: int):
    with db_session(app) as db:
        user = db.get(User, uid)
        return [(m.listing.title, m.highlights, m.services_unknown) for m in matching.find(db, user.preference, uid)]


# ---------- de los datos de la zona a servicios ----------

def test_services_from_pois_groups_public_transport():
    assert area_report.services_from(pois("bus", "super")) == "super,transporte"
    assert area_report.services_from(pois("ferri")) == "transporte"
    assert area_report.services_from({}) is None  # sin datos de OSM: no se sabe


def test_run_report_stores_services(app):
    listing_id = listing_in_cala_llonga(app)
    with db_session(app) as db:
        report = AreaReport(listing_id=listing_id, requested_by=1)
        db.add(report)
        db.commit()
        rid = report.id
    area_report.run_report(app.state.session_factory, rid, area_sources.AreaSources(full_http()), None)
    with db_session(app) as db:
        services = set(db.get(AreaReport, rid).services.split(","))
    assert {"super", "farmacia", "transporte", "playas", "restaurantes"} <= services
    assert "salud" not in services


# ---------- búsqueda ----------

def test_wanted_services_rank_and_highlight_without_excluding(app):
    agency = add_agency(app)
    near = add_listing(app, agency, "1", title="Con todo cerca")
    far = add_listing(app, agency, "2", title="Aislado")
    unknown = add_listing(app, agency, "3", title="Sin analizar")
    add_zone(app, near, "super", "farmacia")
    add_zone(app, far)
    uid = add_user(app, "ana@example.org", areas="ibiza", wanted_services="super,farmacia")
    rows = find(app, uid)
    assert [r[0] for r in rows][0] == "Con todo cerca"
    assert {r[0] for r in rows} == {"Con todo cerca", "Aislado", "Sin analizar"}
    near_row = next(r for r in rows if r[0] == "Con todo cerca")
    assert "súper cerca" in near_row[1] and "farmacia cerca" in near_row[1]
    assert next(r for r in rows if r[0] == "Sin analizar")[2] is True
    assert next(r for r in rows if r[0] == "Aislado")[2] is False


def test_required_services_exclude_known_zones_without_them(app):
    agency = add_agency(app)
    add_zone(app, add_listing(app, agency, "1", title="Con súper y bus"), "super", "bus")
    add_zone(app, add_listing(app, agency, "2", title="Solo súper"), "super")
    add_listing(app, agency, "3", title="Sin analizar")
    uid = add_user(app, "ana@example.org", areas="ibiza", wanted_services="super,transporte", services_required=True)
    assert {r[0] for r in find(app, uid)} == {"Con súper y bus", "Sin analizar"}


def test_no_wanted_services_changes_nothing(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", title="Uno")
    uid = add_user(app, "ana@example.org", areas="ibiza")
    rows = find(app, uid)
    assert rows == [("Uno", (), False)]


def test_summary_mentions_services():
    pref = SearchPreference(areas="ibiza", min_bedrooms=1, wanted_services="super,playas", services_required=True)
    assert "solo con súper y playa cerca" in criteria.summary(pref)
    pref.services_required = False
    assert "mejor con súper y playa cerca" in criteria.summary(pref)


# ---------- formularios ----------

def test_profile_form_saves_wanted_services(app):
    uid = add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    page = client.get("/busqueda").text
    assert "Servicios cerca" in page and all(label in page for label in catalog.SERVICES.values())
    data = {"csrf_token": csrf(client, "/busqueda"), "areas": "ibiza", "min_bedrooms": "1", "furnished": "any",
            "wanted_services": ["super", "transporte", "inventado"], "services_required": "1"}
    client.post("/busqueda", data=data)
    with db_session(app) as db:
        pref = db.get(User, uid).preference
        assert (pref.wanted_services, pref.services_required) == ("super,transporte", True)
    assert 'value="super" checked' in client.get("/busqueda").text


def test_saved_search_keeps_wanted_services(app):
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    data = {"csrf_token": csrf(client, "/busquedas/nueva"), "name": "Con cole", "areas": "ibiza",
            "min_bedrooms": "1", "furnished": "any", "wanted_services": "educacion"}
    client.post("/busquedas", data=data)
    with db_session(app) as db:
        search = db.scalar(select(SavedSearch))
        assert search.wanted_services == "educacion" and not search.services_required


def test_unknown_zone_tag_is_shown_on_results(app):
    add_listing(app, add_agency(app), "1", title="Sin analizar")
    add_user(app, "ana@example.org", areas="ibiza", wanted_services="super")
    assert "servicios por comprobar" in login(app, "ana@example.org").get("/pisos").text


# ---------- análisis en segundo plano ----------

class RecordingRunner(area_report.AreaReportRunner):
    def __init__(self, factory):
        super().__init__(factory, llm=None, sources=area_sources.AreaSources(full_http()))
        self.submitted = []

    def submit(self, report_id: int) -> None:
        self.submitted.append(report_id)


def test_scan_does_nothing_when_nobody_wants_services(app):
    add_listing(app, add_agency(app), "1")
    add_user(app, "ana@example.org", areas="ibiza")
    runner = RecordingRunner(app.state.session_factory)
    assert runner.scan_once() == 0 and runner.submitted == []


def test_scan_queues_unanalysed_listings_in_wanted_areas(app):
    agency = add_agency(app, areas="ibiza,mallorca")
    pending = add_listing(app, agency, "1")
    done = add_listing(app, agency, "2")
    add_zone(app, done, "super")
    add_listing(app, agency, "3", area="mallorca")  # zona que nadie busca con servicios
    add_listing(app, agency, "4", eligible=False)
    add_user(app, "ana@example.org", areas="ibiza", wanted_services="super")
    runner = RecordingRunner(app.state.session_factory)
    assert runner.scan_once() == 1
    with db_session(app) as db:
        report = db.get(AreaReport, runner.submitted[0])
        assert report.listing_id == pending and report.status == "queued" and report.requested_by is None
    assert runner.scan_once() == 0  # no se repite


def test_automatic_reports_skip_the_ai_summary(app):
    listing_id = listing_in_cala_llonga(app)
    with db_session(app) as db:
        report = AreaReport(listing_id=listing_id, requested_by=None)
        db.add(report)
        db.commit()
        rid = report.id
    llm = FakeLLM({"lugar": "Cala Llonga", "municipio": "Santa Eulària des Riu"})
    area_report.run_report(app.state.session_factory, rid, area_sources.AreaSources(full_http()), llm)
    with db_session(app) as db:
        report = db.get(AreaReport, rid)
    assert report.status == "done" and report.story_json == "" and report.error == ""
    assert len(llm.prompts) == 1  # solo la localidad, no el resumen


def test_user_can_ask_for_summary_of_an_automatic_report(app):
    listing_id = listing_in_cala_llonga(app)
    add_zone(app, listing_id, "super")
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    page = client.get(f"/pisos/{listing_id}/zona").text
    assert "Pedir el resumen de la IA" in page and "Spar" not in page

    class Runner:
        submitted = []

        def submit(self, rid):
            self.submitted.append(rid)

    app.state.area_runner = Runner()
    client.post(f"/pisos/{listing_id}/zona", data={"csrf_token": csrf(client, f"/pisos/{listing_id}/zona")})
    assert len(Runner.submitted) == 1


def test_report_reuses_its_own_facts_when_only_the_summary_is_missing(app):
    listing_id = listing_in_cala_llonga(app)
    add_zone(app, listing_id, "super")
    with db_session(app) as db:
        report = db.scalar(select(AreaReport))
        report.requested_by, report.status, report.lat, report.lon = 1, "queued", 38.95, 1.52
        db.commit()
        rid = report.id
    http = full_http()
    llm = FakeLLM(lambda prompt: {"lugar": "Cala Llonga", "municipio": "Santa Eulària"} if "ANUNCIO" in prompt
                  else {"resumen": "Zona tranquila.", "a_favor": ["Súper cerca"]})
    area_report.run_report(app.state.session_factory, rid, area_sources.AreaSources(http), llm)
    with db_session(app) as db:
        report = db.get(AreaReport, rid)
    assert json.loads(report.story_json)["resumen"] == "Zona tranquila."
    assert http.calls == []  # los datos ya estaban: no se vuelve a consultar internet


def test_saving_wanted_services_wakes_the_zone_scanner(app):
    woken = []
    app.state.area_runner.wake = lambda: woken.append(True)
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    base = {"areas": "ibiza", "min_bedrooms": "1", "furnished": "any"}
    client.post("/busqueda", data={**base, "csrf_token": csrf(client, "/busqueda")})
    assert woken == []
    client.post("/busqueda", data={**base, "wanted_services": "super", "csrf_token": csrf(client, "/busqueda")})
    assert woken == [True]
