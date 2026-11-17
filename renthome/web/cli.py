"""Tareas de administración desde la terminal.

    python -m renthome.web.cli crear-admin EMAIL [NOMBRE]

La contraseña se pide por teclado (o se lee de la variable RENTHOME_ADMIN_PASSWORD).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from . import security
from .config import PRIVACY_VERSION, load_settings
from .db import Base, make_engine, make_session_factory
from .models import Profile, SearchPreference, User, utcnow


def create_admin(email: str, name: str, password: str) -> str:
    problem = security.password_problem(password)
    if problem:
        raise SystemExit(problem)
    settings = load_settings()
    engine = make_engine(settings.db_path)
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name=name, password_hash="", consent_at=utcnow(),
                        consent_version=PRIVACY_VERSION)
            user.profile = Profile()
            user.preference = SearchPreference()
            db.add(user)
            result = "creado"
        else:
            result = "actualizado"
        user.role = "admin"
        user.is_active = True
        user.password_hash = security.hash_password(password)
        user.session_version = (user.session_version or 0) + 1
        db.commit()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="renthome.web.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("crear-admin", help="crea o actualiza una cuenta de administrador")
    admin.add_argument("email")
    admin.add_argument("nombre", nargs="?", default="Administrador")
    args = parser.parse_args(argv)

    password = os.environ.get("RENTHOME_ADMIN_PASSWORD") or getpass.getpass("Contraseña: ")
    result = create_admin(args.email.strip().lower(), args.nombre, password)
    sys.stdout.write(f"Administrador {args.email} {result}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
