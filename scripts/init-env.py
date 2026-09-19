#!/usr/bin/env python3
"""Create a private environment file once; never replace existing credentials."""

import argparse
import os
from pathlib import Path
import secrets


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать новый приватный .env без перезаписи")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--database", default="crm")
    args = parser.parse_args()
    if not args.database.isidentifier() or not args.database.isascii():
        parser.error("Имя базы: ASCII-буквы, цифры и подчёркивание; не начинается с цифры")
    password = secrets.token_hex(24)
    content = (
        "ENVIRONMENT=development\n"
        f"POSTGRES_DB={args.database}\nPOSTGRES_USER=crm\nPOSTGRES_PASSWORD={password}\n"
        f"DATABASE_URL=postgresql+psycopg://crm:{password}@127.0.0.1:54329/{args.database}\n"
        f"SECRET_KEY={secrets.token_hex(48)}\nSTORAGE_DIR=.runtime/files\n"
        "ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080\n"
        "COMPANY_TIMEZONE=Europe/Moscow\nCLAMAV_HOST=127.0.0.1\nCLAMAV_PORT=3310\nMAX_FILE_SIZE=26214400\n"
    )
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.exit(1, f"{args.output} уже существует; настройки и секреты не изменены.\n")
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    print(f"Создан {args.output}. Секреты не выводятся; права доступа 0600.")


if __name__ == "__main__":
    main()
