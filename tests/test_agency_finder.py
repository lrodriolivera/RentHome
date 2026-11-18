"""Fuentes reales de inmobiliarias: OpenStreetMap y la API de Brave Search (sin sugerencias inventadas)."""
from __future__ import annotations

import pytest

from renthome.netguard import normalise_site
from renthome.web import agency_finder
from renthome.web.area_sources import AreaSources, SourceError

from .test_area_report import FakeHTTP, FakeResponse

CENTER = [{"lat": "38.98", "lon": "1.30", "display_name": "Sant Antoni de Portmany"}]
OSM_AGENCIES = {"elements": [
    {"type": "node", "tags": {"office": "estate_agent", "name": "Multihogar", "website": "https://www.multihogar.es/"}},
    {"type": "node", "tags": {"office": "estate_agent", "name": "Inmo Portmany", "contact:website": "inmoportmany.com/es"}},
    {"type": "node", "tags": {"shop": "estate_agent", "name": "Sin web"}},
    {"type": "node", "tags": {"office": "estate_agent", "name": "Franquicia", "website": "https://www.idealista.com/pro/x"}},
    {"type": "node", "tags": {"office": "estate_agent", "name": "Repetida", "url": "http://multihogar.es"}},
]}
BRAVE_RESULTS = {"web": {"results": [
    {"url": "https://www.idealista.com/alquiler-viviendas/sant-antoni/", "title": "Idealista"},
    {"url": "https://www.inmo-real-ibiza.es/alquiler-anual/", "title": "Inmo Real Ibiza"},
    {"url": "https://www.multihogar.es/alquiler", "title": "Multihogar"},
    {"url": "https://www.facebook.com/inmo", "title": "Facebook"},
]}}


def finder_with(brave_key: str | None = "clave-brave", overpass=None, brave=None) -> tuple[agency_finder.AgencyFinder, FakeHTTP]:
    http = FakeHTTP({
        "nominatim": FakeResponse(200, CENTER),
        "overpass": overpass or FakeResponse(200, OSM_AGENCIES),
        "api.search.brave.com": brave or FakeResponse(200, BRAVE_RESULTS),
    })
    search = agency_finder.BraveSearch(brave_key, http) if brave_key else None
    return agency_finder.AgencyFinder(AreaSources(http), search), http


# ---------- normalización ----------

@pytest.mark.parametrize("raw", ["https://www.inmobiliariadeibiza oeste.com", "https://exa mple.es", "https://sin-punto"])
def test_normalise_site_rejects_malformed_hosts(raw):
    assert normalise_site(raw) is None


# ---------- Brave Search ----------

def test_brave_is_disabled_without_key(monkeypatch):
    monkeypatch.delenv("RENTHOME_BRAVE_API_KEY", raising=False)
    assert agency_finder.BraveSearch.from_env() is None


def test_brave_sends_key_and_spanish_market():
    http = FakeHTTP({"api.search.brave.com": FakeResponse(200, BRAVE_RESULTS)})
    urls = agency_finder.BraveSearch("clave-brave", http).search("inmobiliaria Sant Antoni alquiler anual")
    assert urls[1] == "https://www.inmo-real-ibiza.es/alquiler-anual/"
    _url, kwargs = http.calls[0]
    assert kwargs["headers"]["X-Subscription-Token"] == "clave-brave"
    assert kwargs["params"]["country"] == "ES" and "Sant Antoni" in kwargs["params"]["q"]


def test_brave_errors_are_reported():
    http = FakeHTTP({"api.search.brave.com": FakeResponse(429, {})})
    with pytest.raises(SourceError):
        agency_finder.BraveSearch("clave-brave", http).search("x")


# ---------- buscador combinado ----------

def test_osm_sites_are_real_websites_without_portals_or_duplicates():
    finder, _ = finder_with(brave_key=None)
    found = finder.find("ibiza", "Sant Antoni de Portmany")
    assert found.sites == ["https://www.multihogar.es", "https://inmoportmany.com"]
    assert found.osm == 2 and found.web == 0 and "sin clave" in found.notes


def test_find_combines_osm_and_web_search():
    finder, http = finder_with()
    found = finder.find("ibiza", "Sant Antoni de Portmany")
    assert found.sites == ["https://www.multihogar.es", "https://inmoportmany.com", "https://www.inmo-real-ibiza.es"]
    assert (found.osm, found.web) == (2, 1)
    queries = [kw["params"]["q"] for url, kw in http.calls if "brave" in url]
    assert queries and all("Sant Antoni de Portmany" in q for q in queries)
    overpass = next(kw["data"]["data"] for url, kw in http.calls if "overpass" in url or "interpreter" in url)
    assert "estate_agent" in overpass and "around:" in overpass


def test_whole_area_search_uses_the_area_box():
    finder, http = finder_with(brave_key=None)
    finder.find("mallorca", "")
    overpass = next(kw["data"]["data"] for url, kw in http.calls if "interpreter" in url)
    assert "39.25" in overpass and "around:" not in overpass
    assert not any("nominatim" in url for url, _ in http.calls)


def test_one_source_failing_does_not_stop_the_other():
    finder, _ = finder_with(brave=FakeResponse(500, {}))
    found = finder.find("ibiza", "Sant Antoni de Portmany")
    assert found.sites[:2] == ["https://www.multihogar.es", "https://inmoportmany.com"]
    assert "búsqueda web" in found.notes

    finder, _ = finder_with(overpass=FakeResponse(502, {}))
    found = finder.find("ibiza", "Sant Antoni de Portmany")
    assert found.sites == ["https://www.inmo-real-ibiza.es", "https://www.multihogar.es"]
    assert "OpenStreetMap" in found.notes


def test_unknown_town_still_searches_the_web():
    finder, _ = finder_with()
    finder.sources.http.routes["nominatim"] = FakeResponse(200, [])
    found = finder.find("ibiza", "Pueblo Inexistente")
    assert found.osm == 0 and found.web >= 1 and "no se encontró" in found.notes
