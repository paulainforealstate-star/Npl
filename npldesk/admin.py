"""Administración desde consola: usuarios, copias de seguridad y auditoría.

Ejemplos:
  python -m npldesk.admin create-user --user ana --name "Ana Ruiz" --role analista
  python -m npldesk.admin set-password --user ana
  python -m npldesk.admin deactivate --user ana
  python -m npldesk.admin list-users
  python -m npldesk.admin backup --dest copias/npldesk-2026-10-30.db
  python -m npldesk.admin verify-audit

La base de datos se elige con --db o con la variable NPLDESK_DB (por defecto data/npldesk.db).
Las contraseñas se piden por teclado: nunca van en la línea de órdenes.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from npldesk import auth
from npldesk.db import Database


def _ask_password() -> str:
    first = getpass.getpass(f"Contraseña (mínimo {auth.PASSWORD_MIN_LENGTH} caracteres): ")
    if getpass.getpass("Repite la contraseña: ") != first:
        raise ValueError("Las contraseñas no coinciden")
    return first


def _find(db: Database, username: str) -> dict:
    user = db.get_user_by_name(username)
    if user is None:
        raise ValueError(f"No existe el usuario '{username}'")
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npldesk.admin", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("NPLDESK_DB", "data/npldesk.db"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create-user", help="crea una cuenta")
    c.add_argument("--user", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--email")
    c.add_argument("--role", required=True, choices=auth.ROLES)

    s = sub.add_parser("set-password", help="cambia la contraseña y cierra sus sesiones")
    s.add_argument("--user", required=True)

    d = sub.add_parser("deactivate", help="desactiva la cuenta y cierra sus sesiones")
    d.add_argument("--user", required=True)

    sub.add_parser("list-users", help="lista las cuentas")

    b = sub.add_parser("backup", help="copia la base de datos con la API de SQLite")
    b.add_argument("--dest", required=True)

    sub.add_parser("verify-audit", help="comprueba la cadena de auditoría")

    args = parser.parse_args(argv)
    db = Database(args.db)
    try:
        if args.cmd == "create-user":
            if db.get_user_by_name(args.user):
                raise ValueError(f"Ya existe el usuario '{args.user}'")
            uid = db.create_user(args.user, args.name, args.email, args.role,
                                 auth.hash_password(_ask_password()))
            print(f"Usuario '{args.user}' creado (id {uid}, rol {args.role})")
        elif args.cmd == "set-password":
            user = _find(db, args.user)
            db.update_user(user["id"], password_hash=auth.hash_password(_ask_password()),
                           failed_attempts=0, locked_until=None)
            db.revoke_user_sessions(user["id"])
            print(f"Contraseña de '{args.user}' cambiada; sesiones cerradas")
        elif args.cmd == "deactivate":
            user = _find(db, args.user)
            db.update_user(user["id"], active=0)
            db.revoke_user_sessions(user["id"])
            print(f"Usuario '{args.user}' desactivado; sesiones cerradas")
        elif args.cmd == "list-users":
            for u in db.list_users():
                estado = "activo" if u["active"] else "inactivo"
                print(f"{u['id']:>4}  {u['username']:<20} {u['role']:<14} {estado:<9} {u['full_name']}")
        elif args.cmd == "backup":
            db.backup_to(args.dest)
            print(f"Copia creada en {args.dest}. Pruébala restaurándola en un entorno de pruebas.")
        elif args.cmd == "verify-audit":
            result = db.verify_audit()
            print(("Cadena de auditoría íntegra" if result["ok"] else "¡Cadena de auditoría alterada!")
                  + f" · filas comprobadas: {result['comprobadas']}")
            return 0 if result["ok"] else 2
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
