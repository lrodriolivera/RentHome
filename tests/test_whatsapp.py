"""Envío de resultados por WhatsApp a través del bridge del homelab (solo administradores)."""
from __future__ import annotations

import pytest

from renthome.web import whatsapp
from renthome.web.models import AppState

from .conftest import add_agency, add_listing, add_user, csrf, db_session, login


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.error = error

    def send(self, recipient: str, message: str) -> None:
        if self.error:
            raise self.error
        self.sent.append((recipient, message))


@pytest.fixture()
def sender(app):
    fake = FakeSender()
    app.state.whatsapp = fake
    return fake


def admin_with_listing(app):
    add_user(app, "admin@example.org", role="admin", areas="ibiza", max_price=3000)
    add_listing(app, add_agency(app), "1", title="Piso luminoso en Vila", price=1800, bedrooms=2)
    return login(app, "admin@example.org")


def send(client, recipient: str = "+34 600 11 22 33", **extra):
    data = {"recipient": recipient, "csrf_token": csrf(client, "/pisos"), **extra}
    return client.post("/pisos/whatsapp", data=data, follow_redirects=False)


# ---------- utilidades ----------

@pytest.mark.parametrize("raw, expected", [
    ("+34 600 11 22 33", "34600112233"),
    ("0034-600-112-233", "34600112233"),
    ("(34) 600112233", "34600112233"),
])
def test_normalize_recipient_accepts_international_numbers(raw, expected):
    assert whatsapp.normalize_recipient(raw) == expected


@pytest.mark.parametrize("raw", ["", "600", "hola", "34600112233@s.whatsapp.net", "1" * 16])
def test_normalize_recipient_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        whatsapp.normalize_recipient(raw)


def test_from_env_is_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("RENTHOME_WHATSAPP_URL", raising=False)
    monkeypatch.delenv("RENTHOME_WHATSAPP_TOKEN", raising=False)
    assert whatsapp.WhatsAppSender.from_env() is None


def test_from_env_reads_url_and_token(monkeypatch):
    monkeypatch.setenv("RENTHOME_WHATSAPP_URL", "http://bridge:8080/")
    monkeypatch.setenv("RENTHOME_WHATSAPP_TOKEN", "t" * 32)
    sender = whatsapp.WhatsAppSender.from_env()
    assert sender is not None and sender.url == "http://bridge:8080"


def test_send_posts_to_bridge_with_bearer_token(monkeypatch):
    calls = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"success": True, "message": "ok"}

    def fake_post(url, json, headers, timeout):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)
    whatsapp.WhatsAppSender("http://bridge:8080", "secreto").send("34600112233", "hola")
    assert calls["url"] == "http://bridge:8080/api/send"
    assert calls["json"] == {"recipient": "34600112233", "message": "hola"}
    assert calls["headers"]["Authorization"] == "Bearer secreto"


def test_send_raises_when_bridge_reports_failure(monkeypatch):
    class Response:
        status_code = 500

        @staticmethod
        def json():
            return {"success": False, "message": "not connected"}

    monkeypatch.setattr(whatsapp.requests, "post", lambda *a, **k: Response())
    with pytest.raises(whatsapp.WhatsAppError):
        whatsapp.WhatsAppSender("http://bridge:8080", "secreto").send("34600112233", "hola")


def test_build_message_lists_listings_and_caps_length(app):
    admin = admin_with_listing(app)  # noqa: F841  (crea datos)
    from renthome.web import matching
    from renthome.web.models import User
    with db_session(app) as db:
        user = db.query(User).filter_by(email="admin@example.org").one()
        matches = matching.find(db, user.preference, user.id)
        text = whatsapp.build_message("Mi búsqueda", matches * 30, "https://renthome.test", "/pisos",
                                      sender_name="Luis Rodríguez")
    assert "Enviado por Luis Rodríguez" in text.splitlines()[0]
    assert text.rstrip().endswith("Generado en RentHome · https://renthome.test")
    assert "Piso luminoso en Vila" in text and "1.800 €/mes" in text
    assert "https://agencia.test/piso/1" in text
    assert text.count("https://agencia.test/piso/1") == whatsapp.MAX_LISTINGS_PER_MESSAGE
    assert "https://renthome.test/pisos" in text


# ---------- ruta ----------

def test_admin_sends_results_and_number_is_remembered(app, sender):
    client = admin_with_listing(app)
    assert "Enviar por WhatsApp" in client.get("/pisos").text
    response = send(client)
    assert response.status_code == 303 and response.headers["location"] == "/pisos"
    assert len(sender.sent) == 1
    recipient, message = sender.sent[0]
    assert recipient == "34600112233" and "Piso luminoso en Vila" in message
    assert "Enviado por admin" in message and "Generado en RentHome" in message
    page = client.get("/pisos").text
    assert "Enviado por WhatsApp" in page
    assert 'value="34600112233"' in page  # aparece entre los números recientes


def test_invalid_number_is_rejected_without_sending(app, sender):
    client = admin_with_listing(app)
    send(client, recipient="hola")
    assert sender.sent == []
    assert "número" in client.get("/pisos").text


def test_bridge_failure_shows_error_and_does_not_crash(app):
    app.state.whatsapp = FakeSender(error=whatsapp.WhatsAppError("caído"))
    client = admin_with_listing(app)
    response = send(client)
    assert response.status_code == 303
    assert "No se pudo enviar" in client.get("/pisos").text


def test_regular_users_cannot_use_whatsapp(app, sender):
    admin_with_listing(app)
    add_user(app, "ana@example.org", areas="ibiza")
    client = login(app, "ana@example.org")
    assert "Enviar por WhatsApp" not in client.get("/pisos").text
    assert send(client).status_code == 404
    assert sender.sent == []


def test_whatsapp_hidden_when_not_configured(app):
    app.state.whatsapp = None
    client = admin_with_listing(app)
    assert "Enviar por WhatsApp" not in client.get("/pisos").text
    assert send(client).status_code == 404


def test_recent_numbers_are_capped(app, sender):
    client = admin_with_listing(app)
    for n in range(whatsapp.MAX_RECENT + 2):
        send(client, recipient=f"3460011223{n}")
    with db_session(app) as db:
        state = db.get(AppState, "wa_recent:1")
        assert len(state.value.split(",")) == whatsapp.MAX_RECENT
        assert state.value.split(",")[0] == f"3460011223{whatsapp.MAX_RECENT + 1}"
