"""Tests de la plataforma web: acceso, invitaciones, perfil, documentos y RGPD."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from renthome.web import crypto, security
from renthome.web.config import MAX_FORM_REQUEST_BYTES, MAX_UPLOAD_REQUEST_BYTES
from renthome.web.models import AuditLog, Document, Invitation, Profile, SearchPreference, User, utcnow
from renthome.web.routes_privacy import purge_stale

from .conftest import PASSWORD, PDF, add_agency, add_listing, add_user, csrf, db_session, login, upload

# ---------- acceso ----------

def test_pages_require_login(app):
    client = TestClient(app)
    for path in ("/", "/perfil", "/busqueda", "/documentos", "/mis-datos", "/admin/usuarios"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_login_rejects_wrong_password_and_missing_csrf(app):
    add_user(app, "ana@example.org")
    client = TestClient(app)
    bad = client.post("/login", data={"email": "ana@example.org", "password": "mala", "csrf_token": csrf(client)})
    assert bad.status_code == 401
    assert client.post("/login", data={"email": "ana@example.org", "password": PASSWORD}).status_code == 403


def test_login_blocks_after_repeated_failures(app):
    add_user(app, "ana@example.org")
    client = TestClient(app)
    token = csrf(client)
    for _ in range(security.LOGIN_MAX_FAILURES):
        client.post("/login", data={"email": "ana@example.org", "password": "mala", "csrf_token": token})
    blocked = client.post("/login", data={"email": "ana@example.org", "password": PASSWORD, "csrf_token": token})
    assert blocked.status_code == 429


def test_security_headers_present(app):
    response = TestClient(app).get("/login")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


# ---------- invitaciones ----------

def invite(app, admin: TestClient, email: str) -> str:
    page = admin.post("/admin/invitaciones", data={"email": email, "csrf_token": csrf(admin, "/admin/usuarios")}).text
    assert "/invitacion/" not in admin.get("/admin/usuarios").text  # el enlace se muestra una sola vez
    return re.search(r"/invitacion/([\w-]+)", page).group(1)


def test_invitation_creates_account_with_consent(app):
    add_user(app, "admin@example.org", role="admin")
    token = invite(app, login(app, "admin@example.org"), "nuevo@example.org")
    guest = TestClient(app)
    form = {"name": "Nuevo Usuario", "password": PASSWORD, "password2": PASSWORD,
            "csrf_token": csrf(guest, f"/invitacion/{token}")}
    assert guest.post(f"/invitacion/{token}", data=form).status_code == 400  # falta el consentimiento
    created = guest.post(f"/invitacion/{token}", data={**form, "consent": "1"}, follow_redirects=False)
    assert created.status_code == 303
    with db_session(app) as db:
        user = db.scalar(select(User).where(User.email == "nuevo@example.org"))
        assert user.consent_at is not None and user.role == "user"
    assert TestClient(app).get(f"/invitacion/{token}").status_code == 404  # un solo uso


def test_invitation_token_is_stored_hashed_and_expires(app):
    add_user(app, "admin@example.org", role="admin")
    token = invite(app, login(app, "admin@example.org"), "nuevo@example.org")
    with db_session(app) as db:
        inv = db.scalar(select(Invitation))
        assert inv.token_hash != token and inv.token_hash == security.token_hash(token)
        inv.expires_at = utcnow() - dt.timedelta(minutes=1)
        db.commit()
    assert TestClient(app).get(f"/invitacion/{token}").status_code == 404


def test_admin_area_hidden_from_regular_users(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    assert client.get("/admin/usuarios").status_code == 404
    assert client.post("/admin/invitaciones", data={"email": "x@example.org",
                                                     "csrf_token": csrf(client, "/perfil")}).status_code == 404


def test_deactivated_user_loses_session(app):
    add_user(app, "admin@example.org", role="admin")
    uid = add_user(app, "ana@example.org")
    ana, admin = login(app, "ana@example.org"), login(app, "admin@example.org")
    admin.post(f"/admin/usuarios/{uid}/estado", data={"csrf_token": csrf(admin, "/admin/usuarios")})
    assert ana.get("/perfil", follow_redirects=False).status_code == 303


# ---------- perfil y preferencias ----------

def test_profile_saves_and_encrypts_id_number(app):
    uid = add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    client.post("/perfil", data={"name": "Ana Pérez", "phone": "600111222", "id_type": "dni",
                                 "id_number": "12345678z", "employment_status": "indefinido",
                                 "net_monthly_income": "2.400", "adults": "2", "csrf_token": csrf(client, "/perfil")})
    with db_session(app) as db:
        profile = db.get(Profile, uid)
        assert profile.net_monthly_income == 2400 and profile.adults == 2
        assert b"12345678Z" not in profile.id_number_enc
    assert 'value="12345678Z"' in client.get("/perfil").text


def test_profile_rejects_invalid_number(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    response = client.post("/perfil", data={"name": "Ana", "net_monthly_income": "mucho",
                                            "csrf_token": csrf(client, "/perfil")})
    assert response.status_code == 400


def test_preferences_filter_dashboard_listings(app):
    agency = add_agency(app)
    add_listing(app, agency, "1", title="Piso dos hab", price=1500)
    add_listing(app, agency, "2", title="Piso caro", price=1900)
    add_listing(app, agency, "3", title="De temporada", price=900, eligible=False)
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    assert "Piso dos hab" not in client.get("/").text  # sin zonas todavía
    client.post("/busqueda", data={"areas": ["ibiza", "inventada"], "max_price": "1600", "min_bedrooms": "2",
                                   "csrf_token": csrf(client, "/busqueda")})
    page = client.get("/").text
    assert "Piso dos hab" in page and "Piso caro" not in page and "De temporada" not in page
    with db_session(app) as db:
        assert db.scalar(select(SearchPreference)).areas == "ibiza"


def test_preferences_require_an_area(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    assert client.post("/busqueda", data={"max_price": "1500", "csrf_token": csrf(client, "/busqueda")}).status_code == 400


# ---------- documentos ----------

def test_document_is_encrypted_on_disk_and_downloadable(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    assert upload(client).status_code == 303
    with db_session(app) as db:
        doc = db.scalar(select(Document))
    stored = (app.state.settings.uploads_dir / doc.storage_name).read_bytes()
    assert PDF not in stored and b"%PDF" not in stored
    download = client.get(f"/documentos/{doc.id}/descargar")
    assert download.content == PDF and "attachment" in download.headers["content-disposition"]


def test_document_of_another_user_is_not_reachable(app):
    add_user(app, "ana@example.org")
    add_user(app, "luis@example.org")
    upload(login(app, "ana@example.org"))
    with db_session(app) as db:
        doc_id = db.scalar(select(Document)).id
    luis = login(app, "luis@example.org")
    assert luis.get(f"/documentos/{doc_id}/descargar").status_code == 404
    assert luis.post(f"/documentos/{doc_id}/borrar", data={"csrf_token": csrf(luis, "/documentos")}).status_code == 404


def test_upload_rejects_disguised_and_oversized_files(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    upload(client, content=b"MZ ejecutable", name="nomina.pdf")
    upload(client, content=b"")
    with db_session(app) as db:
        assert db.scalar(select(Document)) is None


def test_key_of_one_user_cannot_decrypt_another(app):
    blob = crypto.encrypt("k" * 40, 1, "document", b"secreto")
    assert crypto.decrypt("k" * 40, 1, "document", blob) == b"secreto"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("k" * 40, 2, "document", blob)


# ---------- derechos RGPD ----------

def test_export_contains_profile_and_documents(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    upload(client)
    response = client.post("/mis-datos/exportar", data={"csrf_token": csrf(client, "/mis-datos")})
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    data = json.loads(archive.read("mis-datos.json"))
    assert data["cuenta"]["email"] == "ana@example.org" and "password_hash" not in data["cuenta"]
    assert any(name.startswith("documentos/") and archive.read(name) == PDF for name in archive.namelist())


def test_account_deletion_erases_everything(app):
    uid = add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    upload(client)
    with db_session(app) as db:
        stored = app.state.settings.uploads_dir / db.scalar(select(Document)).storage_name
    wrong = client.post("/mis-datos/eliminar", data={"password": "otra", "csrf_token": csrf(client, "/mis-datos")},
                        follow_redirects=False)
    assert wrong.headers["location"] == "/mis-datos" and stored.exists()
    client.post("/mis-datos/eliminar", data={"password": PASSWORD, "csrf_token": csrf(client, "/mis-datos")})
    with db_session(app) as db:
        assert db.get(User, uid) is None and db.get(Profile, uid) is None and db.scalar(select(Document)) is None
        assert db.scalar(select(AuditLog).where(AuditLog.user_id == uid)) is None
    assert not stored.exists()


def test_inactive_accounts_are_purged_but_admins_kept(app):
    old = add_user(app, "vieja@example.org")
    admin = add_user(app, "admin@example.org", role="admin")
    recent = add_user(app, "ana@example.org")
    with db_session(app) as db:
        for uid in (old, admin):
            db.get(User, uid).created_at = utcnow() - dt.timedelta(days=400)
        db.commit()
        assert purge_stale(db, app.state.settings) == 1
        db.commit()
        assert db.get(User, old) is None and db.get(User, admin) is not None and db.get(User, recent) is not None


# ---------- endurecimiento ----------

def test_oversized_bodies_are_rejected_before_processing(app):
    client = TestClient(app)
    big_form = client.post("/login", content=b"a" * (MAX_FORM_REQUEST_BYTES + 1),
                           headers={"content-type": "application/x-www-form-urlencoded"})
    assert big_form.status_code == 413
    big_upload = client.post("/documentos", content=b"a" * (MAX_UPLOAD_REQUEST_BYTES + 1),
                             headers={"content-type": "multipart/form-data; boundary=x"})
    assert big_upload.status_code == 413


def test_anonymous_upload_is_redirected_without_storing(app):
    response = TestClient(app).post("/documentos", files={"file": ("a.pdf", PDF, "application/pdf")},
                                    data={"kind": "nominas"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_user_ids_are_never_reused(app):
    first = add_user(app, "ana@example.org")
    with db_session(app) as db:
        db.delete(db.get(User, first))
        db.commit()
    assert add_user(app, "luis@example.org") > first


def test_logout_invalidates_copied_session_cookie(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    stolen = TestClient(app, cookies=dict(client.cookies))
    assert stolen.get("/perfil", follow_redirects=False).status_code == 200
    client.post("/logout", data={"csrf_token": csrf(client, "/perfil")})
    assert stolen.get("/perfil", follow_redirects=False).status_code == 303


def test_password_guessing_with_open_session_is_limited(app):
    add_user(app, "ana@example.org")
    client = login(app, "ana@example.org")
    for _ in range(security.LOGIN_MAX_FAILURES + 1):
        client.post("/mis-datos/eliminar", data={"password": "mala", "csrf_token": csrf(client, "/mis-datos")})
    client.post("/mis-datos/eliminar", data={"password": PASSWORD, "csrf_token": csrf(client, "/mis-datos")})
    with db_session(app) as db:
        assert db.scalar(select(User)) is not None  # bloqueado: ni con la buena se borra


def test_limiter_memory_is_bounded():
    limiter = security.LoginLimiter()
    for i in range(security.LIMITER_MAX_KEYS + 500):
        limiter.record_failure(f"email:{i}")
    assert len(limiter._failures) <= security.LIMITER_MAX_KEYS


def test_privacy_policy_is_public(app):
    page = TestClient(app).get("/privacidad")
    assert page.status_code == 200 and "r@example.org" in page.text
