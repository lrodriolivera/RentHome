"""Tests de la protección frente a SSRF del rastreador."""
from __future__ import annotations

from sqlalchemy import select

from renthome import netguard
from renthome.crawl import discover
from renthome.fetch import Fetcher
from renthome.web import agencies
from renthome.web.crawl_service import crawl_agency
from renthome.web.models import Listing

from .conftest import add_agency, db_session
from .test_search import LIST_URL, FakeFetcher, detail, piso

import pytest


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8080/x", "http://10.0.0.5:3000/", "http://100.109.70.110:8082/v1",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://servicio/",
    "http://nextcloud.internal/", "http://mac.tailnet.ts.net/", "http://buena.com@127.0.0.1/", "ftp://agencia.com/",
    "javascript:alert(1)//agencia.com/piso/1", "https://agencia.com:8443/", "https://agencia.com/" + "a" * 600,
])
def test_internal_and_odd_urls_are_not_public(url):
    assert not netguard.looks_public(url) and not netguard.is_public_url(url)


def test_public_looking_names_are_accepted_syntactically():
    assert netguard.looks_public("https://www.inmobiliaria-del-barrio.es/alquiler?page=2")
    assert netguard.normalise_site("WWW.Agencia.es/alquiler") == "https://www.agencia.es"
    assert netguard.normalise_site("http://usuario@10.0.0.1/") is None


def test_name_resolving_to_private_address_is_rejected(monkeypatch):
    netguard._host_resolves_public.cache_clear()
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("10.1.2.3" if "malo" in host else "93.184.216.34", 0))])
    assert netguard.is_public_url("https://bueno.example.org/") and not netguard.is_public_url("https://malo.example.org/")
    netguard._host_resolves_public.cache_clear()


def test_redirect_to_internal_address_is_not_followed(monkeypatch):
    class Response:
        def __init__(self, status, location=None, text=""):
            self.status_code, self.headers, self.text = status, ({"Location": location} if location else {}), text
            self.encoding, self.apparent_encoding = "utf-8", "utf-8"

    visited = []
    fetcher = Fetcher("test", delay=0, url_guard=lambda url: "interno" not in url)

    def fake_get(url, **kwargs):
        visited.append(url)
        assert kwargs.get("allow_redirects") is False
        return Response(302, "http://interno:3000/secreto") if "agencia" in url else Response(200, text="secreto")

    monkeypatch.setattr(fetcher.session, "get", fake_get)
    assert fetcher.get("https://agencia.example/alquiler") is None
    assert visited == ["https://agencia.example/alquiler"]  # nunca llegó a pedir la dirección interna


def test_links_to_other_hosts_never_become_listings(app):
    """Un listado hostil enlaza a una dirección interna cuya URL «casa» con el patrón de fichas."""
    agency = add_agency(app)
    hostile = ('<a href="http://10.0.0.5:3000/x?q=agencia.test/piso/9">a</a>'
               '<a href="http://otra.test/agencia.test/piso/8">b</a>'
               '<a href="JavaScript:alert(1)//agencia.test/piso/7">c</a>'
               '<a href="/piso/1">ok</a><a href="/piso/1">ok</a>')
    fetcher = FakeFetcher({LIST_URL: f"<html><body>{hostile}</body></html>",
                           piso("1"): detail("Alquiler anual piso 2 dormitorios", "1.500")})
    crawl_agency(app.state.session_factory, agency, fetcher)
    assert fetcher.requested == [LIST_URL, piso("1")]
    with db_session(app) as db:
        assert [l.url for l in db.scalars(select(Listing))] == [piso("1")]


def test_pagination_stays_on_the_agency_site():
    pages = {"https://agencia.test/alquiler": '<a href="http://10.0.0.5/alquiler?page=2">2</a><a href="/alquiler?page=3">3</a>',
             "https://agencia.test/alquiler?page=3": '<a href="/piso/5">x</a>'}
    fetcher = FakeFetcher(pages)
    urls, _ = discover(fetcher, {"start_urls": ["https://agencia.test/alquiler"], "detail_pattern": r"/piso/\d+$",
                                 "page_pattern": r"alquiler\?page=\d+", "max_pages": 5})
    assert urls == ["https://agencia.test/piso/5"] and not any("10.0.0.5" in u for u in fetcher.requested)


def test_listing_url_owned_by_another_agency_does_not_break_the_crawl(app):
    first, second = add_agency(app, "Una"), add_agency(app, "Otra")
    pages = {LIST_URL: '<a href="/piso/1">a</a><a href="/piso/2">b</a>',
             piso("1"): detail("Alquiler anual piso 2 dormitorios", "1.500"),
             piso("2"): detail("Alquiler anual piso 1 dormitorio", "1.100")}
    crawl_agency(app.state.session_factory, first, FakeFetcher({k: v for k, v in pages.items() if k != piso("2")}))
    result = crawl_agency(app.state.session_factory, second, FakeFetcher(pages))
    assert not result.error
    with db_session(app) as db:
        owners = {l.url: l.agency_id for l in db.scalars(select(Listing))}
    assert owners == {piso("1"): first, piso("2"): second}


def test_agency_config_rejects_internal_urls_and_dangerous_patterns():
    ok = (["https://agencia.es/alquiler"], r"agencia\.es/piso/\d+", "")
    agencies.validate_config(*ok)
    for start_urls, detail_pattern, page_pattern in (
        (["http://10.0.0.5/alquiler"], ok[1], ""), (["http://127.0.0.1.nip.io:8080/"], ok[1], ""),
        (ok[0], r"(a+)+$", ""), (ok[0], ok[1], r"(.*)*x"), (ok[0], "a" * 301, ""),
    ):
        with pytest.raises(agencies.AgencyConfigError):
            agencies.validate_config(start_urls, detail_pattern, page_pattern)
