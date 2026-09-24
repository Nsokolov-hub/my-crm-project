#!/usr/bin/env python3
"""Provision seven demo users in Docker and copy credentials to a private host file."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILE = PROJECT_ROOT / ".runtime" / "demo-role-credentials.txt"


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=PROJECT_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать семь демо-аккаунтов на чистой базе CRM")
    parser.add_argument("--url", default="http://127.0.0.1:8080/", help="Адрес открываемой CRM")
    args = parser.parse_args()
    if LOCAL_FILE.exists() or LOCAL_FILE.is_symlink():
        raise SystemExit(f"{LOCAL_FILE} уже существует. Учётные записи и пароли не изменены.")
    if LOCAL_FILE.parent.is_symlink():
        raise SystemExit("Каталог .runtime не должен быть символической ссылкой.")
    LOCAL_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    remote_file = f"/tmp/crm-demo-roles-{secrets.token_hex(12)}.txt"
    try:
        compose(
            "exec", "-T", "api", "python", "-m", "app.demo_accounts",
            "--credentials-file", remote_file, "--url", args.url,
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit("Не удалось создать аккаунты. Проверьте, что стек запущен на чистой базе.") from error

    host_file_created = False
    try:
        descriptor = os.open(LOCAL_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        host_file_created = True
        with os.fdopen(descriptor, "wb") as stream:
            subprocess.run(
                ["docker", "compose", "exec", "-T", "api", "cat", remote_file],
                cwd=PROJECT_ROOT,
                stdout=stream,
                check=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, subprocess.CalledProcessError) as error:
        if host_file_created:
            LOCAL_FILE.unlink(missing_ok=True)
        raise SystemExit(
            f"Аккаунты созданы, но копирование паролей не удалось. Не запускайте скрипт повторно. "
            f"Исходный файл оставлен в контейнере: {remote_file}. Скопируйте его через docker compose cp."
        ) from error
    compose("exec", "-T", "api", "rm", "--", remote_file)
    print(f"Доступы к семи ролям: {LOCAL_FILE} (права 0600). Не включайте этот файл в архив.")


if __name__ == "__main__":
    main()
