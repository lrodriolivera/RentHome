"""Google Places (Text Search) como fuente de webs de inmobiliarias, sin pasar del cupo gratuito."""
from __future__ import annotations

import pytest

from renthome.web import agency_finder
from renthome.web.agency_finder import GooglePlaces, MonthlyBudget
from renthome.web.area_sources import AreaSources, SourceError
from renthome.web.models import AppState

from .conftest import db_session
from .test_agency_finder import CENTER, OSM_AGENCIES
from .test_area_report import FakeHTTP, FakeResponse

PLACES = {"places": [
    {"websiteUri": "https://www.inmo-calamar.es/alquiler"},
    {"websiteUri": "https://www.multihogar.es/"},
    {"websiteUri": "https://www.idealista.com/pro/x"},
    {},
]}


class Budget:
    def __init__(self, left: int) -> None:
        self.left, self.taken = left, 0

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left, self.taken = self.left - 1, self.taken + 1
        return True


def places(http: FakeHTTP, budget=None) -> GooglePlaces:
    return GooglePlaces("clave-google", budget or Budget(10), http)


# ---------- cliente ----------

def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("RENTHOME_GOOGLE_PLACES_KEY", raising=False)
    assert GooglePlaces.from_env(None) is None


def test_asks_only_for_the_website_inside_the_area():
    http = FakeHTTP({"places.googleapis.com": FakeResponse(200, PLACES)})
    urls = places(http).search("inmobiliaria en Sóller, Mallorca", "mallorca")
    assert urls == ["https://www.inmo-calamar.es/alquiler", "https://www.multihogar.es/",
                    "https://www.idealista.com/pro/x"]
    url, kwargs = http.calls[0]
    assert url == "https://places.googleapis.com/v1/places:searchText"
    assert kwargs["headers"]["X-Goog-Api-Key"] == "clave-google"
    assert kwargs["headers"]["X-Goog-FieldMask"] == "places.websiteUri"  # nada más: sin datos que guardar
    body = kwargs["json"]
    assert body["textQuery"] == "inmobiliaria en Sóller, Mallorca" and body["regionCode"] == "ES"
    box = body["locationRestriction"]["rectangle"]
    assert box["low"] == {"latitude": 39.25, "longitude": 2.30}
    assert box["high"] == {"latitude": 39.97, "longitude": 3.50}


def test_errors_are_reported_without_the_key():
    http = FakeHTTP({"places.googleapis.com": FakeResponse(403, {"error": {"message": "API key not valid"}})})
    with pytest.raises(SourceError) as exc:
        places(http).search("x", "ibiza")
    assert "403" in str(exc.value) and "clave-google" not in str(exc.value)


def test_no_request_when_the_monthly_budget_is_spent():
    http = FakeHTTP({"places.googleapis.com": FakeResponse(200, PLACES)})
    with pytest.raises(SourceError) as exc:
        places(http, Budget(0)).search("x", "ibiza")
    assert "cupo" in str(exc.value) and http.calls == []


# ---------- contador mensual ----------

def test_monthly_budget_counts_in_the_database(app):
    budget = MonthlyBudget(app.state.session_factory, "gplaces", limit=2, month=lambda: "2026-11")
    assert [budget.take(), budget.take(), budget.take()] == [True, True, False]
    with db_session(app) as db:
        assert db.get(AppState, "gplaces:2026-11").value == "2"
    assert budget.used() == 2
    next_month = MonthlyBudget(app.state.session_factory, "gplaces", limit=2, month=lambda: "2026-12")
    assert next_month.take() is True


def test_monthly_limit_never_exceeds_the_free_cap(monkeypatch):
    monkeypatch.setenv("RENTHOME_GOOGLE_PLACES_KEY", "clave")
    monkeypatch.setenv("RENTHOME_GOOGLE_PLACES_MONTHLY", "5000")
    google = GooglePlaces.from_env(None)
    assert google.budget.limit == agency_finder.GOOGLE_FREE_MONTHLY
    monkeypatch.setenv("RENTHOME_GOOGLE_PLACES_MONTHLY", "100")
    assert GooglePlaces.from_env(None).budget.limit == 100


# ---------- dentro del buscador ----------

def finder(budget=None) -> tuple[agency_finder.AgencyFinder, FakeHTTP]:
    http = FakeHTTP({"nominatim": FakeResponse(200, CENTER), "overpass": FakeResponse(200, OSM_AGENCIES),
                     "places.googleapis.com": FakeResponse(200, PLACES)})
    return agency_finder.AgencyFinder(AreaSources(http), google=places(http, budget)), http


def test_town_search_uses_one_google_request():
    budget = Budget(10)
    search, http = finder(budget)
    found = search.find("ibiza", "Sant Antoni de Portmany")
    assert found.sites == ["https://www.multihogar.es", "https://inmoportmany.com", "https://www.inmo-calamar.es"]
    assert (found.osm, found.web) == (2, 1) and budget.taken == 1
    query = next(kw["json"]["textQuery"] for url, kw in http.calls if "places.googleapis" in url)
    assert "Sant Antoni de Portmany" in query and "Ibiza" in query
    assert search.web_search_ready and "Google" in search.web_sources


def test_spent_budget_is_a_note_not_a_failure():
    search, _ = finder(Budget(0))
    found = search.find("ibiza", "Sant Antoni de Portmany")
    assert found.osm == 2 and found.web == 0 and "cupo" in found.notes
