"""Tests de la búsqueda: rastreo compartido, emparejamiento, búsquedas guardadas e inmobiliarias."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from renthome.web import features, geo
from renthome.web.crawl_service import DETAIL_REFRESH_DAYS, crawl_agency
from renthome.web.models import Agency, CrawlJob, Listing, SavedSearch, UserListing, utcnow

from .conftest import add_agency, add_listing, add_user, clear_agencies, csrf, db_session, login

LIST_URL = "https://agencia.test/alquiler"


def detail(title: str, price: str, extra: str = "") -> str:
    return f"<html><body><h1>{title}</h1><div>Alquiler mensual: {price} €/mes</div><p>{extra}</p></body></html>"


def listing_page(*slugs: str, rented: str = "") -> str:
    cards = "".join(
        f'<div><a href="/piso/{s}">foto</a>{"<span>Alquilado</span>" if s == rented else ""}<a href="/piso/{s}">ver</a></div>'
        for s in slugs)
    return f"<html><body>{cards}</body></html>"


class FakeFetcher:
    """Sirve páginas de un diccionario y cuenta las peticiones."""

    def __init__(self, pages: dict[str, str]):
        self.pages, self.requested = pages, []

    def get(self, url: str, render: bool = False):
        self.requested.append(url)
        return self.pages.get(url)

    def render_available(self) -> bool:
        return False


def piso(n: str) -> str:
    return f"https://agencia.test/piso/{n}"


# ---------- rastreo ----------

def test_crawl_stores_and_classifies_listings(app):
    agency = add_agency(app)
    fetcher = FakeFetcher({
        LIST_URL: listing_page("1", "2", "3"),
        piso("1"): detail("Alquiler anual: piso de 2 dormitorios en Santa Eulalia", "1.650",
                          "Amueblado, con ascensor y terraza. 75 m2. No se admiten mascotas."),
        piso("2"): detail("Alquiler de temporada de invierno, ático en Ibiza", "1.200"),
        piso("3"): detail("Plaza de parking en alquiler anual", "120"),
    })
    result = crawl_agency(app.state.session_factory, agency, fetcher)
    assert result.found == 3 and result.new == 1 and not result.error
    with db_session(app) as db:
        by_url = {l.url: l for l in db.scalars(select(Listing))}
        good = by_url[piso("1")]
        assert good.eligible and good.price == 1650 and good.bedrooms == 2 and good.place == "Santa Eulària"
        assert (good.furnished, good.pets, good.has_elevator, good.has_terrace, good.size_m2) == ("yes", "no", True, True, 75)
        assert not by_url[piso("2")].eligible and not by_url[piso("3")].eligible
        assert db.get(Agency, agency).last_status == ""


def test_crawl_is_incremental_and_tracks_removed_and_rented(app):
    agency = add_agency(app)
    pages = {LIST_URL: listing_page("1", "2"),
             piso("1"): detail("Alquiler anual piso 2 dormitorios", "1.500"),
             piso("2"): detail("Alquiler anual piso 1 dormitorio", "1.100")}
    crawl_agency(app.state.session_factory, agency, FakeFetcher(pages))

    second = FakeFetcher({**pages, LIST_URL: listing_page("1", "4", rented="1"),
                          piso("4"): detail("Alquiler anual estudio", "900")})
    crawl_agency(app.state.session_factory, agency, second)
    assert second.requested == [LIST_URL, piso("4")]  # no vuelve a abrir las fichas ya conocidas
    with db_session(app) as db:
        by_url = {l.url: l for l in db.scalars(select(Listing))}
        assert by_url[piso("1")].status == "rented" and not by_url[piso("1")].eligible
        assert not by_url[piso("2")].active and by_url[piso("4")].active


def test_crawl_rechecks_old_details_and_records_price_drop(app):
    agency = add_agency(app)
    pages = {LIST_URL: listing_page("1"), piso("1"): detail("Alquiler anual piso 2 dormitorios", "1.800")}
    crawl_agency(app.state.session_factory, agency, FakeFetcher(pages))
    with db_session(app) as db:
        db.scalar(select(Listing)).detail_checked_at = utcnow() - dt.timedelta(days=DETAIL_REFRESH_DAYS + 1)
        db.commit()
    crawl_agency(app.state.session_factory, agency,
                 FakeFetcher({**pages, piso("1"): detail("Alquiler anual piso 2 dormitorios", "1.600")}))
    with db_session(app) as db:
        listing = db.scalar(select(Listing))
        assert (listing.price, listing.price_prev) == (1600, 1800)


def test_crawl_failure_keeps_existing_listings(app):
    agency = add_agency(app)
    add_listing(app, agency, "1")
    result = crawl_agency(app.state.session_factory, agency, FakeFetcher({}))
    assert result.error
    with db_session(app) as db:
        assert db.scalar(select(Listing)).active
        assert db.get(Agency, agency).consecutive_failures == 1


# ---------- zona y características ----------

def test_geo_prefers_agency_area_for_ambiguous_names():
    assert geo.detect(["Piso en Sant Antoni"], ["ibiza"]) == ("ibiza", "Sant Antoni")
    assert geo.detect(["Piso en Sant Antoni"], ["barcelona"]) == ("barcelona", "Eixample")
    assert geo.detect(["Piso obra nueva Sants-Montjuïc"], ["ibiza"]) == ("barcelona", "Sants-Montjuïc")
    assert geo.detect(["Apartamento luminoso", "santa-ponsa-vistas"], ["mallorca"]) == ("mallorca", "Calvià")
    assert geo.detect(["Sin pistas"], ["menorca"]) == ("menorca", "")


def test_features_detect_type_and_explicit_negatives():
    found = features.detect("Ático dúplex sin amueblar", "Tercero sin ascensor. Garaje opcional. 110 m².")
    assert (found.property_type, found.furnished, found.has_elevator, found.has_parking, found.size_m2) == \
        ("atico", "no", False, True, 110)
    assert features.detect("Casa adosada", "").property_type == "adosado"
    assert features.detect("Bonita vivienda", "").property_type == ""


# ---------- resultados por usuario ----------

def test_results_apply_each_users_criteria(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", title="Barato dos", price=1400)
    add_listing(app, agency, "2", title="Caro dos", price=2400)
    add_listing(app, agency, "3", title="Estudio", price=900, bedrooms=0)
    add_listing(app, agency, "4", title="Sin precio", price=None)
    add_listing(app, agency, "5", title="En Mallorca", area="mallorca")
    add_listing(app, agency, "6", title="Sin muebles", furnished="no")
    add_listing(app, agency, "7", title="Inactivo", active=False)
    add_user(app, "ana@example.org", areas="ibiza", max_price=2000, min_bedrooms=1, furnished="yes")
    add_user(app, "luis@example.org", areas="ibiza,mallorca", max_price=3000, min_bedrooms=0)
    ana, luis = login(app, "ana@example.org").get("/pisos").text, login(app, "luis@example.org").get("/pisos").text
    assert all(t in ana for t in ("Barato dos", "Sin precio")) and \
        not any(t in ana for t in ("Caro dos", "Estudio", "En Mallorca", "Sin muebles", "Inactivo"))
    assert all(t in luis for t in ("Barato dos", "Caro dos", "Estudio", "En Mallorca", "Sin muebles"))


def test_preferred_bedrooms_and_zone_rank_first(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", title="AAA barato", price=1000, bedrooms=1)
    add_listing(app, agency, "2", title="BBB ideal", price=1900, bedrooms=2)
    add_user(app, "ana@example.org", areas="ibiza", max_price=2000, min_bedrooms=1, preferred_bedrooms=2)
    page = login(app, "ana@example.org").get("/pisos").text
    assert page.index("BBB ideal") < page.index("AAA barato")


def test_hidden_listings_disappear_and_saved_show_in_favourites(app):
    agency = add_agency(app)
    keep, drop = add_listing(app, agency, "1", title="Me gusta"), add_listing(app, agency, "2", title="No me gusta")
    add_user(app, "ana@example.org", areas="ibiza", max_price=2000)
    client = login(app, "ana@example.org")
    token = csrf(client, "/pisos")
    client.post(f"/pisos/{keep}/estado", data={"state": "saved", "next": "/pisos", "csrf_token": token})
    client.post(f"/pisos/{drop}/estado", data={"state": "hidden", "next": "/pisos", "csrf_token": token})
    page = client.get("/pisos").text
    assert "Me gusta" in page and "No me gusta" not in page
    assert "Me gusta" in client.get("/favoritos").text
    client.post("/favoritos/restaurar-descartados", data={"csrf_token": token})
    assert "No me gusta" in client.get("/pisos").text


def test_state_redirect_stays_on_site(app):
    agency = add_agency(app)
    listing = add_listing(app, agency, "1")
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    response = client.post(f"/pisos/{listing}/estado", follow_redirects=False,
                           data={"state": "saved", "next": "//evil.example/x", "csrf_token": csrf(client, "/pisos")})
    assert response.headers["location"] == "/pisos"


def test_refresh_enqueues_only_stale_agencies_in_user_areas(app):
    clear_agencies(app)
    stale, _other = add_agency(app, "Ibiza Uno"), add_agency(app, "Mallorca Uno", areas="mallorca")
    add_agency(app, "Ibiza Fresca", last_crawled_at=utcnow())
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    client.post("/pisos/actualizar", data={"csrf_token": csrf(client, "/pisos")})
    client.post("/pisos/actualizar", data={"csrf_token": csrf(client, "/pisos")})  # repetir no duplica
    with db_session(app) as db:
        assert [j.agency_id for j in db.scalars(select(CrawlJob))] == [stale]
    assert client.get("/api/rastreo?areas=ibiza").json() == {"pending": 1}


# ---------- búsquedas guardadas ----------

def test_saved_search_lifecycle_and_isolation(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", title="En Mallorca", area="mallorca", price=1700)
    add_user(app, "ana@example.org", areas="ibiza", max_price=1500)
    add_user(app, "luis@example.org", areas="ibiza")
    ana = login(app, "ana@example.org")
    created = ana.post("/busquedas", follow_redirects=False,
                       data={"name": "Mallorca", "areas": "mallorca", "max_price": "2000", "min_bedrooms": "1",
                             "notify": "1", "csrf_token": csrf(ana, "/busquedas/nueva")})
    assert created.status_code == 303
    with db_session(app) as db:
        search = db.scalar(select(SavedSearch))
    assert "En Mallorca" in ana.get(f"/pisos?b={search.id}").text and "En Mallorca" not in ana.get("/pisos").text
    assert "Mallorca" in ana.get("/busquedas").text

    luis = login(app, "luis@example.org")
    assert luis.get(f"/pisos?b={search.id}").status_code == 404
    assert luis.post(f"/busquedas/{search.id}/borrar", data={"csrf_token": csrf(luis, "/busquedas")}).status_code == 404

    ana.post(f"/busquedas/{search.id}/borrar", data={"csrf_token": csrf(ana, "/busquedas")})
    with db_session(app) as db:
        assert db.scalar(select(SavedSearch)) is None


def test_saved_search_marks_listings_new_since_last_visit(app):
    agency = add_agency(app)
    add_user(app, "ana@example.org", areas="ibiza")
    ana = login(app, "ana@example.org")
    ana.post("/busquedas", data={"name": "Ibiza", "areas": "ibiza", "csrf_token": csrf(ana, "/busquedas/nueva")})
    add_listing(app, agency, "1", title="Recién llegado")
    assert "1 nuevo" in ana.get("/busquedas").text
    with db_session(app) as db:
        search_id = db.scalar(select(SavedSearch)).id
    ana.get(f"/pisos?b={search_id}")
    assert "1 nuevo" not in ana.get("/busquedas").text


def test_deleting_account_removes_searches_and_listing_states(app):
    agency = add_agency(app)
    listing = add_listing(app, agency, "1")
    add_user(app, "ana@example.org", areas="ibiza")
    ana = login(app, "ana@example.org")
    ana.post("/busquedas", data={"name": "Ibiza", "areas": "ibiza", "csrf_token": csrf(ana, "/busquedas/nueva")})
    ana.post(f"/pisos/{listing}/estado", data={"state": "saved", "csrf_token": csrf(ana, "/pisos")})
    ana.post("/mis-datos/eliminar", data={"password": "clave-segura-123", "csrf_token": csrf(ana, "/mis-datos")})
    with db_session(app) as db:
        assert db.scalar(select(SavedSearch)) is None and db.scalar(select(UserListing)) is None
        assert db.get(Listing, listing) is not None  # los anuncios son compartidos


# ---------- administración de inmobiliarias ----------

def agency_form(**overrides) -> dict:
    return {"name": "Nueva Agencia", "town": "Palma", "website": "https://nueva.test", "areas": "mallorca",
            "start_urls": "https://nueva.test/alquiler", "detail_pattern": r"nueva\.test/piso/\d+",
            "max_pages": "3", "long_term_section": "1", **overrides}


def test_admin_manages_agencies(app):
    add_user(app, "admin@example.org", role="admin")
    admin = login(app, "admin@example.org")
    token = csrf(admin, "/admin/inmobiliarias")
    assert admin.post("/admin/inmobiliarias", data={**agency_form(detail_pattern="(sin cerrar"), "csrf_token": token}).status_code == 400
    assert admin.post("/admin/inmobiliarias", data={**agency_form(start_urls="javascript:alert(1)"), "csrf_token": token}).status_code == 400
    admin.post("/admin/inmobiliarias", data={**agency_form(), "csrf_token": token})
    with db_session(app) as db:
        agency = db.scalar(select(Agency).where(Agency.name == "Nueva Agencia"))
        assert agency.areas == "mallorca" and agency.long_term_section and agency.origin == "admin"
    admin.post(f"/admin/inmobiliarias/{agency.id}/rastrear", data={"csrf_token": token})
    admin.post(f"/admin/inmobiliarias/{agency.id}/estado", data={"csrf_token": token})
    with db_session(app) as db:
        assert [j.reason for j in db.scalars(select(CrawlJob))] == ["admin"] and not db.get(Agency, agency.id).enabled
    admin.post(f"/admin/inmobiliarias/{agency.id}/eliminar", data={"csrf_token": token})
    with db_session(app) as db:
        assert db.get(Agency, agency.id) is None


def test_agency_admin_hidden_from_users(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    assert client.get("/admin/inmobiliarias").status_code == 404


def test_seed_agencies_loaded_on_start(app):
    with db_session(app) as db:
        names = set(db.scalars(select(Agency.name)))
    assert {"Living in Ibiza", "Roxire"} <= names


# ---------- avisos ----------

def test_saved_search_email_digest_is_sent_once(app):
    from renthome.web.notifications import notify_saved_searches

    class FakeMailer:
        sent: list = []

        def send(self, to, subject, body):
            self.sent.append((to, subject, body))

    agency = add_agency(app)
    add_user(app, "ana@example.org", areas="ibiza")
    ana = login(app, "ana@example.org")
    ana.post("/busquedas", data={"name": "Ibiza", "areas": "ibiza", "notify": "1", "csrf_token": csrf(ana, "/busquedas/nueva")})
    mailer, factory = FakeMailer(), app.state.session_factory
    assert notify_saved_searches(factory, mailer, "https://rh.test") == 0  # nada nuevo todavía
    add_listing(app, agency, "1", title="Recién llegado")
    assert notify_saved_searches(factory, mailer, "https://rh.test") == 1
    to, subject, body = mailer.sent[0]
    assert to == "ana@example.org" and "Recién llegado" in body and "https://rh.test/pisos?b=" in body
    add_listing(app, agency, "2", title="Otro más")
    assert notify_saved_searches(factory, mailer, "https://rh.test") == 0  # como mucho uno cada 12 h
    assert notify_saved_searches(factory, None, "") == 0
