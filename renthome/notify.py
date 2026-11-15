"""Avisa de los anuncios nuevos de la última búsqueda.

Canales (se usan los que estén configurados):

* ntfy (notificación al móvil): NTFY_URL, p. ej. https://ntfy.sh/mis-pisos-ibiza-x7k2
  (opcional NTFY_TOKEN si tu servidor ntfy requiere autenticación)
* Email por SMTP: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, MAIL_TO
  Con Gmail: SMTP_HOST=smtp.gmail.com y una "contraseña de aplicación".

MAIL_ALWAYS=1 envía el resumen aunque no haya anuncios nuevos.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.request
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def send_email(subject: str, body_md: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(body_md)
    try:
        import markdown
        msg.add_alternative(markdown.markdown(body_md), subtype="html")
    except ImportError:
        pass
    port = int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    print("Email enviado a", os.environ["MAIL_TO"])


def send_ntfy(subject: str, body_md: str):
    headers = {"Title": subject.encode("utf-8"), "Markdown": "yes", "Tags": "house"}
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    if os.environ.get("PUBLIC_URL"):
        headers["Click"] = os.environ["PUBLIC_URL"]
    req = urllib.request.Request(os.environ["NTFY_URL"], data=body_md.encode("utf-8"),
                                 headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=30).read()
    print("Aviso ntfy enviado")


def main() -> int:
    last_file = ROOT / "data" / "last_run.json"
    if not last_file.exists():
        print("No hay resultados todavía")
        return 0
    last = json.loads(last_file.read_text(encoding="utf-8"))
    if not last.get("new") and os.environ.get("MAIL_ALWAYS") != "1":
        print("Sin anuncios nuevos: no se envía aviso")
        return 0
    body_md = (ROOT / "data" / "notification.md").read_text(encoding="utf-8")
    subject = f"🏠 {len(last.get('new', []))} piso(s) nuevo(s) en Ibiza – {last.get('date')}"

    sent = False
    if os.environ.get("NTFY_URL"):
        try:
            send_ntfy(subject, body_md)
            sent = True
        except Exception as e:  # noqa: BLE001
            print("Error enviando ntfy:", e)
    if os.environ.get("SMTP_HOST") and os.environ.get("MAIL_TO"):
        try:
            send_email(subject, body_md)
            sent = True
        except Exception as e:  # noqa: BLE001
            print("Error enviando email:", e)
    if not sent:
        print("Ningún canal de aviso configurado (NTFY_URL o SMTP_*)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
