"""Envía por email los anuncios nuevos (opcional).

Variables de entorno: SMTP_HOST, SMTP_PORT (587 por defecto), SMTP_USER,
SMTP_PASSWORD, MAIL_TO (varios separados por comas).
Con Gmail: SMTP_HOST=smtp.gmail.com y una "contraseña de aplicación".
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    last = json.loads((ROOT / "data" / "last_run.json").read_text(encoding="utf-8"))
    if not last.get("new") and os.environ.get("MAIL_ALWAYS") != "1":
        print("Sin anuncios nuevos: no se envía email")
        return 0
    body_md = (ROOT / "data" / "notification.md").read_text(encoding="utf-8")
    try:
        import markdown
        body_html = markdown.markdown(body_md)
    except ImportError:
        body_html = None

    msg = EmailMessage()
    msg["Subject"] = f"🏠 {len(last.get('new', []))} piso(s) nuevo(s) en Ibiza – {last.get('date')}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(body_md)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    port = int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    print("Email enviado a", os.environ["MAIL_TO"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
