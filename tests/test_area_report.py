"""Informe de la zona de un piso: OpenStreetMap, Wikipedia, Wikimedia Commons y resumen de la IA."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from renthome.web import area_report, area_sources
from renthome.web.ai import LLMUnavailable
from renthome.web.models import AreaReport

from .conftest import add_agency, add_listing, add_user, csrf, db_session, login
from .test_ai import FakeLLM

LAT, LON = 38.9526, 1.5205


# ---------- dobles de prueba ----------

class FakeResponse:
    def __init__(self, status: int, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


class FakeHTTP:
    """Responde según el dominio; apunta cada llamada."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def _answer(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for key, value in self.routes.items():
            if key in url:
                return value(url, kwargs) if callable(value) else value
        return FakeResponse(404, {})

    def get(self, url, **kwargs):
        return self._answer(url, **kwargs)

    def post(self, url, **kwargs):
        return self._answer(url, **kwargs)


OSM_ELEMENTS = [
    {"type": "node", "lat": 38.9530, "lon": 1.5210, "tags": {"shop": "supermarket", "name": "Spar"}},
    {"type": "node", "lat": 38.9600, "lon": 1.5300, "tags": {"shop": "convenience", "name": "Badia"}},
    {"type": "node", "lat": 38.9527, "lon": 1.5206, "tags": {"highway": "bus_stop", "name": "Cala Llonga"}},
    {"type": "way", "center": {"lat": 38.9510, "lon": 1.5230}, "tags": {"natural": "beach", "name": "Platja de Cala Llonga"}},
    {"type": "node", "lat": 38.9531, "lon": 1.5207, "tags": {"amenity": "restaurant", "name": "SOS Restaurant"}},
    {"type": "node", "lat": 38.9531, "lon": 1.5207, "tags": {"amenity": "pharmacy"}},
    {"type": "node", "lat": 38.9531, "lon": 1.5207, "tags": {"amenity": "bench"}},
]


def full_http(overpass=None) -> FakeHTTP:
    return FakeHTTP({
        "nominatim": FakeResponse(200, [{"lat": str(LAT), "lon": str(LON), "display_name": "Cala Llonga, Santa Eulària des Riu"}]),
        "overpass": overpass or FakeResponse(200, {"elements": OSM_ELEMENTS}),
        "wikipedia.org": lambda url, kw: FakeResponse(200, (
            {"query": {"geosearch": [{"pageid": 1, "title": "Puig d'en Valls", "dist": 900},
                                     {"pageid": 2, "title": "Cala Llonga", "dist": 1380}]}}
            if kw.get("params", {}).get("list") == "geosearch" else
            {"query": {"pages": {"2": {"pageid": 2, "title": "Cala Llonga", "extract": "Cala Llonga es una cala y "
                                       "núcleo turístico del municipio de Santa Eulària des Riu.",
                                       "fullurl": "https://es.wikipedia.org/wiki/Cala_Llonga"}}}})),
        "commons.wikimedia.org": FakeResponse(200, {"query": {"pages": {
            "1": {"title": "File:Cala Llonga playa.jpg", "imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/a/cala.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cala.jpg",
                "extmetadata": {"Artist": {"value": "<a href='x'>Ana Fotógrafa</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]},
            "2": {"title": "File:Mapa de Ibiza.svg", "imageinfo": [{"thumburl": "https://upload.wikimedia.org/a/mapa.png",
                                                                  "descriptionurl": "https://commons.wikimedia.org/wiki/File:Mapa.svg"}]},
            "3": {"title": "File:Otra foto.jpg", "imageinfo": [{"thumburl": "https://evil.example/x.jpg",
                                                              "descriptionurl": "https://evil.example"}]},
            "4": {"title": "File:Puerto de Cala Llonga 1.jpg", "imageinfo": [{
                "thumburl": "https://thumb.wikimedia.org/c/puerto1.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:P1.jpg"}]},
            "5": {"title": "File:Puerto de Cala Llonga 2.jpg", "imageinfo": [{
                "thumburl": "https://thumb.wikimedia.org/c/puerto2.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:P2.jpg"}]},
        }}}),
    })


def listing_in_cala_llonga(app, **fields) -> int:
    agency = add_agency(app, website="https://agencia.test")
    return add_listing(app, agency, fields.pop("slug", "1"), title="Estupendo Apartamento en Cala Llonga",
                       place="Santa Eulària", area="ibiza", snippet="Piso cerca de la playa de Cala Llonga", **fields)


# ---------- fuentes ----------

@pytest.mark.parametrize("tags, expected", [
    ({"shop": "supermarket"}, "super"), ({"amenity": "pharmacy"}, "farmacia"), ({"amenity": "clinic"}, "salud"),
    ({"amenity": "school"}, "educacion"), ({"highway": "bus_stop"}, "bus"), ({"railway": "station"}, "tren"),
    ({"amenity": "ferry_terminal"}, "ferri"), ({"amenity": "cafe"}, "restaurantes"), ({"amenity": "cinema"}, "ocio"),
    ({"leisure": "fitness_centre"}, "deporte"), ({"leisure": "park"}, "parques"), ({"natural": "beach"}, "playas"),
    ({"amenity": "bench"}, None), ({"leisure": "swimming_pool"}, None),  # suelen ser piscinas privadas
])
def test_classify_osm_tags(tags, expected):
    assert area_sources.classify(tags) == expected


def test_summarise_pois_counts_and_sorts_by_distance():
    summary = area_sources.summarise_pois(OSM_ELEMENTS, LAT, LON)
    supers = summary["super"]
    assert supers["count"] == 2 and supers["nearest"][0]["name"] == "Spar"
    assert supers["nearest"][0]["distance_m"] < supers["nearest"][1]["distance_m"]
    assert summary["farmacia"]["count"] == 1 and summary["farmacia"]["nearest"][0]["name"] == "Farmacia"
    assert summary["playas"]["nearest"][0]["name"] == "Platja de Cala Llonga"
    assert summary["salud"]["count"] == 0


def test_geocode_restricts_search_to_the_area():
    http = full_http()
    location = area_sources.AreaSources(http).geocode("Cala Llonga, Santa Eulària", "ibiza")
    assert (location.lat, location.lon) == (LAT, LON)
    params = http.calls[0][1]["params"]
    assert params["bounded"] == 1 and params["countrycodes"] == "es" and params["viewbox"]
    assert "RentHome" in http.calls[0][1]["headers"]["User-Agent"]


def test_geocode_returns_none_when_nothing_found():
    http = FakeHTTP({"nominatim": FakeResponse(200, [])})
    assert area_sources.AreaSources(http).geocode("Sitio Inexistente", "ibiza") is None


def test_nearby_falls_back_to_next_overpass_server():
    answers = iter([FakeResponse(502, {}), FakeResponse(200, {"elements": OSM_ELEMENTS})])
    http = full_http(overpass=lambda url, kw: next(answers))
    summary = area_sources.AreaSources(http).nearby(LAT, LON, "ibiza")
    assert summary["super"]["count"] == 2
    servers = [url for url, _ in http.calls if "overpass" in url or "interpreter" in url]
    assert len(servers) == 2 and servers[0] != servers[1]


def test_photos_keep_only_free_wikimedia_photos_with_credit():
    photos = area_sources.AreaSources(full_http()).photos(LAT, LON, "ibiza")
    assert [p["src"] for p in photos] == ["https://upload.wikimedia.org/a/cala.jpg",
                                          "https://thumb.wikimedia.org/c/puerto1.jpg"]  # sin repetir la misma serie
    assert photos[0]["author"] == "Ana Fotógrafa" and photos[0]["license"] == "CC BY-SA 4.0"


def test_wikipedia_prefers_article_named_like_the_place():
    wiki = area_sources.AreaSources(full_http()).wikipedia(LAT, LON, ["Cala Llonga", "Santa Eulària"])
    assert wiki["title"] == "Cala Llonga" and "núcleo turístico" in wiki["extract"]
    assert wiki["url"] == "https://es.wikipedia.org/wiki/Cala_Llonga"


# ---------- localidad e informe ----------

def test_locality_uses_ai_only_when_the_place_appears_in_the_ad(app):
    listing_in_cala_llonga(app)
    with db_session(app) as db:
        from renthome.web.models import Listing
        listing = db.scalar(select(Listing))
        good = FakeLLM({"lugar": "Cala Llonga", "municipio": "Santa Eulària des Riu"})
        assert area_report.locality_for(listing, good) == ("Cala Llonga", "Santa Eulària des Riu")
        invented = FakeLLM({"lugar": "Talamanca", "municipio": "Eivissa"})
        assert area_report.locality_for(listing, invented) == ("Santa Eulària", "Santa Eulària")
        assert area_report.locality_for(listing, None) == ("Santa Eulària", "Santa Eulària")
        assert area_report.locality_for(listing, FakeLLM(fail=True)) == ("Santa Eulària", "Santa Eulària")


def story_answer(prompt: str) -> dict:
    if "lugar" in prompt.lower() and "ANUNCIO" in prompt:
        return {"lugar": "Cala Llonga", "municipio": "Santa Eulària des Riu"}
    return {"resumen": "Cala tranquila con playa a pocos pasos. Más info en https://spam.example",
            "ambiente": "Zona residencial y turística.", "servicios": "Spar a 60 m.",
            "transporte": "Parada de bus junto al piso.", "ocio": "Restaurantes junto a la playa.",
            "a_favor": ["Playa a 300 m", "Supermercado cerca"], "a_tener_en_cuenta": ["Poca vida en invierno"]}


def create_report(app, listing_id: int) -> int:
    with db_session(app) as db:
        report = AreaReport(listing_id=listing_id, requested_by=1)
        db.add(report)
        db.commit()
        return report.id


def test_run_report_gathers_facts_and_story(app):
    listing_id = listing_in_cala_llonga(app)
    report_id = create_report(app, listing_id)
    llm = FakeLLM(story_answer)
    area_report.run_report(app.state.session_factory, report_id, area_sources.AreaSources(full_http()), llm)
    with db_session(app) as db:
        report = db.get(AreaReport, report_id)
        facts, story = json.loads(report.facts_json), json.loads(report.story_json)
    assert report.status == "done" and report.locality == "Cala Llonga"
    assert facts["pois"]["super"]["nearest"][0]["name"] == "Spar" and facts["wiki"]["title"] == "Cala Llonga"
    assert len(facts["photos"]) == 2
    assert story["a_favor"] == ["Playa a 300 m", "Supermercado cerca"]
    assert "spam.example" not in story["resumen"]  # sin enlaces generados por el modelo
    assert "Spar" in llm.prompts[-1]  # el resumen se basa en los datos reunidos
    assert "no aparece en el mapa" in area_report.STORY_SYSTEM  # un 0 de OSM no significa que no exista


def test_report_is_useful_without_ai(app):
    report_id = create_report(app, listing_in_cala_llonga(app))
    area_report.run_report(app.state.session_factory, report_id, area_sources.AreaSources(full_http()), None)
    with db_session(app) as db:
        report = db.get(AreaReport, report_id)
    assert report.status == "done" and json.loads(report.facts_json)["pois"]["bus"]["count"] == 1
    assert report.story_json in ("", "{}") and "IA" in report.error


def test_report_fails_cleanly_when_place_cannot_be_located(app):
    report_id = create_report(app, listing_in_cala_llonga(app))
    http = FakeHTTP({"nominatim": FakeResponse(200, [])})
    area_report.run_report(app.state.session_factory, report_id, area_sources.AreaSources(http), None)
    with db_session(app) as db:
        report = db.get(AreaReport, report_id)
    assert report.status == "failed" and "ubicar" in report.error


def test_same_place_reuses_previous_report(app):
    first = create_report(app, listing_in_cala_llonga(app))
    area_report.run_report(app.state.session_factory, first, area_sources.AreaSources(full_http()), FakeLLM(story_answer))
    second_listing = add_listing(app, add_agency(app, name="Otra", website="https://otra.test"), "2",
                                 title="Piso en Cala Llonga", place="Santa Eulària", area="ibiza")
    second = create_report(app, second_listing)
    http = full_http()
    area_report.run_report(app.state.session_factory, second, area_sources.AreaSources(http), FakeLLM(story_answer))
    with db_session(app) as db:
        report = db.get(AreaReport, second)
    assert report.status == "done" and json.loads(report.facts_json)["wiki"]["title"] == "Cala Llonga"
    assert http.calls == []  # no se repiten consultas a internet


# ---------- páginas ----------

class RecordingRunner:
    def __init__(self):
        self.submitted = []

    def submit(self, report_id: int) -> None:
        self.submitted.append(report_id)


@pytest.fixture()
def runner(app):
    fake = RecordingRunner()
    app.state.area_runner = fake
    return fake


def test_ticket_links_to_area_page(app, runner):
    add_user(app, "ana@example.org", areas="ibiza")
    listing_id = listing_in_cala_llonga(app)
    client = login(app, "ana@example.org")
    assert f"/pisos/{listing_id}/zona" in client.get("/pisos").text
    page = client.get(f"/pisos/{listing_id}/zona").text
    assert "Investigar la zona" in page


def test_requesting_a_report_queues_it_once(app, runner):
    add_user(app, "ana@example.org", areas="ibiza")
    listing_id = listing_in_cala_llonga(app)
    client = login(app, "ana@example.org")
    token = csrf(client, f"/pisos/{listing_id}/zona")
    page = client.post(f"/pisos/{listing_id}/zona", data={"csrf_token": token}).text
    assert "Preparando" in page and 'http-equiv="refresh"' in page
    client.post(f"/pisos/{listing_id}/zona", data={"csrf_token": token})
    with db_session(app) as db:
        assert len(db.scalars(select(AreaReport)).all()) == 1
    assert len(runner.submitted) == 1


def test_finished_report_is_shown_with_sources_and_credits(app, runner):
    add_user(app, "ana@example.org", areas="ibiza")
    listing_id = listing_in_cala_llonga(app)
    report_id = create_report(app, listing_id)
    area_report.run_report(app.state.session_factory, report_id, area_sources.AreaSources(full_http()), FakeLLM(story_answer))
    page = login(app, "ana@example.org").get(f"/pisos/{listing_id}/zona").text
    for text in ("Cala tranquila", "Spar", "Platja de Cala Llonga", "Ana Fotógrafa", "CC BY-SA 4.0",
                 "https://upload.wikimedia.org/a/cala.jpg", "es.wikipedia.org/wiki/Cala_Llonga", "OpenStreetMap",
                 "Playa a 300 m"):
        assert text in page, text
    assert 'http-equiv="refresh"' not in page


def test_area_requests_are_rate_limited(app, runner, monkeypatch):
    monkeypatch.setattr(area_report, "MAX_REPORTS_PER_DAY", 1)
    add_user(app, "ana@example.org", areas="ibiza")
    first = listing_in_cala_llonga(app)
    second = add_listing(app, add_agency(app, name="Otra", website="https://otra.test"), "2", place="Sant Antoni",
                         area="ibiza")
    client = login(app, "ana@example.org")
    client.post(f"/pisos/{first}/zona", data={"csrf_token": csrf(client, f"/pisos/{first}/zona")})
    page = client.post(f"/pisos/{second}/zona", data={"csrf_token": csrf(client, f"/pisos/{second}/zona")}).text
    assert "límite" in page and len(runner.submitted) == 1


def test_area_page_requires_login_and_existing_listing(app, runner):
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/pisos/1/zona", follow_redirects=False).status_code == 303
    add_user(app, "ana@example.org")
    assert login(app, "ana@example.org").get("/pisos/999/zona").status_code == 404
