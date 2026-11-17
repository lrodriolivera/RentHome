"""Ayudantes comunes de los tests de la plataforma web."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from renthome.web import security
from renthome.web.app import create_app
from renthome.web.config import Settings
from renthome.web.models import Agency, Listing, Profile, SearchPreference, User, utcnow

PASSWORD = "clave-segura-123"
PDF = b"%PDF-1.4 contenido de prueba"


@pytest.fixture()
def app(tmp_path):
    settings = Settings(data_dir=tmp_path, secret_key="k" * 40, cookie_secure=False,
                        controller_name="Responsable", controller_email="r@example.org", crawl_enabled=False)
    return create_app(settings)


def db_session(app):
    return app.state.session_factory()


def add_user(app, email: str, role: str = "user", **pref) -> int:
    with db_session(app) as db:
        user = User(email=email, name=email.split("@")[0], password_hash=security.hash_password(PASSWORD),
                    role=role, consent_at=utcnow(), consent_version="t")
        user.profile, user.preference = Profile(), SearchPreference(**pref)
        db.add(user)
        db.commit()
        return user.id


def add_agency(app, name: str = "Agencia Test", areas: str = "ibiza", **extra) -> int:
    with db_session(app) as db:
        agency = Agency(name=name, areas=areas, start_urls="https://agencia.test/alquiler",
                        detail_pattern=r"agencia\.test/piso/\d+$", long_term_section=True, **extra)
        db.add(agency)
        db.commit()
        return agency.id


def clear_agencies(app) -> None:
    """Quita las inmobiliarias iniciales para tests que cuentan trabajos de rastreo."""
    with db_session(app) as db:
        for agency in db.query(Agency).all():
            db.delete(agency)
        db.commit()


def add_listing(app, agency_id: int, slug: str, **fields) -> int:
    values = {"title": f"Piso {slug}", "price": 1500, "bedrooms": 2, "area": "ibiza", "term": "long",
              "eligible": True, "active": True, **fields}
    with db_session(app) as db:
        listing = Listing(agency_id=agency_id, url=f"https://agencia.test/piso/{slug}", **values)
        db.add(listing)
        db.commit()
        return listing.id


def csrf(client: TestClient, path: str = "/login") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)


def login(app, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/login", data={"email": email, "password": PASSWORD, "csrf_token": csrf(client)},
                           follow_redirects=False)
    assert response.status_code == 303
    return client


def upload(client: TestClient, content: bytes = PDF, name: str = "nomina.pdf", kind: str = "nominas"):
    return client.post("/documentos", data={"kind": kind, "csrf_token": csrf(client, "/documentos")},
                       files={"file": (name, content, "application/pdf")}, follow_redirects=False)
