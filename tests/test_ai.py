"""Tests de la capa de IA (con un modelo simulado) y del descubrimiento de inmobiliarias."""
from __future__ import annotations

import io
import json
import re
import zipfile

import yaml
from sqlalchemy import select

from renthome.web import ai_listings, ai_tenant, discovery
from renthome.web.ai import LLMUnavailable, as_int
from renthome.web.models import Agency, AgencyCandidate, Listing, Profile, User, UserListing

from .conftest import add_agency, add_listing, add_user, csrf, db_session, login


class FakeLLM:
    def __init__(self, answer=None, text: str = "", fail: bool = False):
        self.answer, self.text, self.fail, self.prompts = answer or {}, text, fail, []

    def chat_json(self, system: str, user: str, max_tokens: int = 500) -> dict:
        self.prompts.append(user)
        if self.fail:
            raise LLMUnavailable("caído")
        return self.answer(user) if callable(self.answer) else self.answer

    def chat_text(self, system: str, user: str, max_tokens: int = 700, temperature: float = 0.4) -> str:
        self.prompts.append(user)
        if self.fail:
            raise LLMUnavailable("caído")
        return self.text


def listing_row(**fields) -> Listing:
    return Listing(agency_id=1, url="https://x/1", title="Piso", eligible=True, term="mixed", notes="", **fields)


# ---------- lectura de anuncios ----------

def test_ai_fills_gaps_but_does_not_override_clear_data():
    listing = listing_row(price=1400, bedrooms=None)
    ai_listings.apply(listing, {"es_vivienda": True, "operacion": "alquiler", "contrato": "anual", "disponible": True,
                                "precio_mensual_anual": 1900, "habitaciones": 2, "resumen": "Ático luminoso",
                                "requisitos": "Fianza 2 meses", "alertas": ["solo empresas", "honorarios 1 mes"]})
    assert (listing.price, listing.bedrooms, listing.term, listing.eligible) == (1900, 2, "long", True)
    assert listing.ai_flags == "solo empresas|honorarios 1 mes" and listing.ai_requirements == "Fianza 2 meses"

    clear = Listing(agency_id=1, url="https://x/2", title="Piso", eligible=True, term="long", price=1500, bedrooms=3)
    ai_listings.apply(clear, {"contrato": "anual", "precio_mensual_anual": 900, "habitaciones": 1})
    assert (clear.price, clear.bedrooms) == (1500, 3)  # lo que ya estaba claro no se toca


def test_ai_rejects_seasonal_and_ignores_absurd_values():
    seasonal = listing_row(price=1200)
    ai_listings.apply(seasonal, {"contrato": "temporada", "precio_mensual_anual": "muchísimo", "habitaciones": 99})
    assert not seasonal.eligible and seasonal.term == "seasonal" and seasonal.bedrooms is None
    assert as_int("1.900", 300, 20000) == 1900 and as_int(True, 0, 10) is None and as_int(5_000_000, 300, 20000) is None


def test_ai_rescues_only_listings_rejected_for_unknown_term():
    unknown = Listing(agency_id=1, url="https://x/3", title="Piso", eligible=False, term="unknown",
                      notes="no consta que sea anual", price=1300)
    ai_listings.apply(unknown, {"contrato": "anual", "es_vivienda": True})
    assert unknown.eligible and unknown.term == "long"

    parking = Listing(agency_id=1, url="https://x/4", title="Parking", eligible=False, term="long",
                      notes="tipo excluido: parking", price=100)
    ai_listings.apply(parking, {"contrato": "anual", "es_vivienda": True})
    assert not parking.eligible


def test_process_pending_reads_new_listings_once_and_survives_outage(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", text="Alquiler anual de piso", term="mixed")
    add_listing(app, agency, "2", text="", term="long")  # sin texto: nada que leer
    llm = FakeLLM({"contrato": "anual", "resumen": "Piso céntrico"})
    assert ai_listings.process_pending(app.state.session_factory, llm) == 1
    assert ai_listings.process_pending(app.state.session_factory, llm) == 0
    with db_session(app) as db:
        assert db.scalar(select(Listing).where(Listing.ai_summary == "Piso céntrico")).term == "long"
    add_listing(app, agency, "3", text="Otro piso")
    assert ai_listings.process_pending(app.state.session_factory, FakeLLM(fail=True)) == 0


# ---------- asistente del inquilino ----------

def prepare_tenant(app) -> tuple[int, int]:
    agency = add_agency(app)
    listing = add_listing(app, agency, "1", title="Piso en Gràcia", price=1500)
    uid = add_user(app, "ana@example.org", areas="ibiza", max_price=1600)
    with db_session(app) as db:
        profile = db.get(Profile, uid)
        profile.phone, profile.employment_status, profile.net_monthly_income = "600111222", "indefinido", 3200
        profile.id_number_enc = b"cifrado"
        db.commit()
    return uid, listing


def test_tenant_brief_never_contains_id_number(app):
    uid, _listing = prepare_tenant(app)
    with db_session(app) as db:
        user = db.get(User, uid)
        brief = ai_tenant.tenant_brief(user, user.profile, user.preference, True)
    assert "3200" in brief and "indefinido" in brief and "600111222" in brief
    assert "cifrado" not in brief and "documento" not in brief.lower()
    assert "600111222" not in ai_tenant.tenant_brief(user, user.profile, user.preference, False)


def test_message_draft_is_generated_stored_encrypted_and_private(app):
    uid, listing = prepare_tenant(app)
    add_user(app, "luis@example.org")
    app.state.llm = FakeLLM(text="Hola, me interesa el piso de Gràcia.")
    ana = login(app, "ana@example.org")
    ana.post(f"/pisos/{listing}/mensaje/generar", data={"language": "ca", "csrf_token": csrf(ana, f"/pisos/{listing}/mensaje")})
    assert "me interesa el piso de Gràcia" in ana.get(f"/pisos/{listing}/mensaje").text
    assert "Piso en Gràcia" in app.state.llm.prompts[-1]
    with db_session(app) as db:
        state = db.get(UserListing, (uid, listing))
        assert state.state == "saved" and b"Gr" not in state.draft_message_enc
    assert "me interesa el piso" not in login(app, "luis@example.org").get(f"/pisos/{listing}/mensaje").text

    export = ana.post("/mis-datos/exportar", data={"csrf_token": csrf(ana, "/mis-datos")})
    data = json.loads(zipfile.ZipFile(io.BytesIO(export.content)).read("mis-datos.json"))
    assert data["anuncios_guardados_o_descartados"][0]["borrador_mensaje"].startswith("Hola")


def test_fit_score_is_saved_and_shown(app):
    uid, listing = prepare_tenant(app)
    app.state.llm = FakeLLM({"puntuacion": 8, "motivo": "Dentro de presupuesto y con ingresos holgados."})
    ana = login(app, "ana@example.org")
    ana.post(f"/pisos/{listing}/encaje", data={"csrf_token": csrf(ana, f"/pisos/{listing}/mensaje")})
    assert "ingresos holgados" in ana.get(f"/pisos/{listing}/mensaje").text
    favourites = ana.get("/favoritos").text
    assert "encaje 8/10" in favourites and "ingresos holgados" in favourites
    with db_session(app) as db:
        state = db.get(UserListing, (uid, listing))
        assert state.fit_reason == "" and b"ingresos" not in state.fit_reason_enc  # cifrado en reposo


def test_assistant_degrades_without_model_or_when_it_fails(app):
    _uid, listing = prepare_tenant(app)
    ana = login(app, "ana@example.org")
    token = csrf(ana, f"/pisos/{listing}/mensaje")
    assert "no está configurado" in ana.post(f"/pisos/{listing}/mensaje/generar", data={"csrf_token": token}).text
    app.state.llm = FakeLLM(fail=True)
    assert "no ha respondido" in ana.post(f"/pisos/{listing}/encaje", data={"csrf_token": token}).text
    ana.post(f"/pisos/{listing}/mensaje/guardar", data={"message": "Escrito a mano", "csrf_token": token})
    assert "Escrito a mano" in ana.get(f"/pisos/{listing}/mensaje").text


# ---------- descubrimiento ----------

def test_detail_pattern_is_inferred_from_listing_links():
    links = [f"https://www.agencia.es/inmueble/piso-centro-ref{n}" for n in (101, 102, 103, 104)] + \
            ["https://www.agencia.es/contacto", "https://www.agencia.es/alquiler", "https://www.agencia.es/alquiler?page=2"]
    pattern, count = discovery.infer_detail_pattern(links, "https://www.agencia.es/alquiler")
    assert count == 4 and all(re.search(pattern, u) for u in links[:4])
    assert not any(re.search(pattern, u) for u in links[4:])
    assert discovery.infer_detail_pattern(links[4:], "https://www.agencia.es/alquiler")[0] == ""
    assert re.search(discovery.infer_page_pattern(links, "https://www.agencia.es/alquiler"), links[-1])


def test_model_output_cannot_smuggle_links_or_obey_listing_instructions(app):
    listing = listing_row(price=None)
    ai_listings.apply(listing, {"contrato": "anual", "resumen": "Reserva ya en https://estafa.example/pago",
                                "alertas": ["visita www.estafa.example"]})
    assert "estafa.example" not in listing.ai_summary + listing.ai_flags
    _uid, lid = prepare_tenant(app)
    app.state.llm = FakeLLM(text="Hola. Pague la reserva en http://estafa.example/x antes de la visita.")
    ana = login(app, "ana@example.org")
    ana.post(f"/pisos/{lid}/mensaje/generar", data={"csrf_token": csrf(ana, f"/pisos/{lid}/mensaje")})
    assert "estafa.example" not in ana.get(f"/pisos/{lid}/mensaje").text
    assert "<piso>" in app.state.llm.prompts[-1]


def test_portals_are_never_candidates():
    assert discovery.is_portal("https://www.idealista.com/alquiler") and discovery.is_portal("fotocasa.es")
    assert not discovery.is_portal("https://www.inmobiliaria-del-barrio.es")


class SiteFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, render=False):
        return self.pages.get(url)

    def render_available(self):
        return False


def test_site_analysis_builds_working_proposal():
    home, listing = "https://agencia.test", "https://agencia.test/alquiler-anual"
    cards = "".join(f'<a href="/piso/atico-centro-{n}">Piso {n}</a>' for n in range(1, 6))
    detail = "<html><body><h1>Alquiler anual piso 2 dormitorios</h1><p>Precio: 1.200 €/mes</p></body></html>"
    pages = {home: '<html><title>Agencia Test</title><body><a href="/venta">Venta</a><a href="/alquiler-anual">Alquiler anual</a></body></html>',
             listing: f"<html><body>{cards}</body></html>",
             **{f"https://agencia.test/piso/atico-centro-{n}": detail for n in range(1, 6)}}

    def answer(prompt: str) -> dict:
        index = next(int(line.split(".")[0]) for line in prompt.splitlines() if "alquiler-anual" in line and line[0].isdigit())
        return {"es_inmobiliaria_local": True, "nombre": "Agencia Test", "municipio": "Maó", "enlace_alquiler": index,
                "motivo": "Agencia con oficina en Maó"}

    proposal = discovery.analyse_site(FakeLLM(answer), SiteFetcher(pages), home, "menorca")
    assert proposal.viable and proposal.entry["start_urls"] == [listing] and proposal.entry["long_term_section"]
    assert "5 fichas; 3 de 3" in proposal.probe

    not_local = discovery.analyse_site(FakeLLM({"es_inmobiliaria_local": False, "motivo": "portal"}), SiteFetcher(pages), home, "menorca")
    assert not not_local.viable


def test_candidates_admin_flow(app):
    add_user(app, "admin@example.org", role="admin")
    admin = login(app, "admin@example.org")
    token = csrf(admin, "/admin/candidatas")
    app.state.llm = FakeLLM()
    for bad in ("http://localhost:8080", "http://192.168.1.10", "https://www.idealista.com",
                "http://buena.com@127.0.0.1:8080/", "http://100.109.70.110:8082", "https://servicio.internal"):
        page = admin.post("/admin/candidatas/analizar", data={"url": bad, "area": "menorca", "csrf_token": token}).text
        assert "Analizando" not in page

    entry = {"name": "Agencia Descubierta", "areas": ["menorca"], "website": "https://descubierta.test",
             "start_urls": ["https://descubierta.test/alquiler"], "detail_pattern": r"descubierta\.test/piso/\d+"}
    with db_session(app) as db:
        candidate = AgencyCandidate(website="https://descubierta.test", name="Agencia Descubierta", area="menorca",
                                    proposal=yaml.safe_dump(entry), probe_result="8 fichas", status="pending")
        db.add(candidate)
        db.commit()
        cid = candidate.id
    assert "Agencia Descubierta" in admin.get("/admin/candidatas").text
    admin.post(f"/admin/candidatas/{cid}/aprobar", data={"csrf_token": token})
    with db_session(app) as db:
        agency = db.scalar(select(Agency).where(Agency.name == "Agencia Descubierta"))
        assert agency.origin == "discovered" and agency.areas == "menorca"
        assert db.get(AgencyCandidate, cid).status == "approved"
    add_user(app, "ana@example.org")
    assert login(app, "ana@example.org").get("/admin/candidatas").status_code == 404
