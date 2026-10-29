"""Gera o hash scrypt da senha do dashboard sem expô-la no histórico do terminal."""

from __future__ import annotations

from getpass import getpass

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass("Senha do dashboard: ")
    if getpass("Confirme a senha: ") != password:
        raise SystemExit("As senhas não conferem.")
    if len(password) < 12:
        raise SystemExit("Use pelo menos 12 caracteres.")
    print(f"DASHBOARD_PASSWORD_HASH='{generate_password_hash(password)}'")


if __name__ == "__main__":
    main()
