#!/usr/bin/env python3
"""Pin the backend dependencies of backend/pyproject.toml; --check fails if the lock files are stale.

requirements.lock holds the runtime set installed into the Docker image, requirements-dev.lock the
same pins plus tests, linters and audit. Run with the development environment, which provides uv.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
COMMAND = "python3 scripts/lock_backend.py"
# The development lock is constrained by the runtime one, so both pin the same versions.
LOCKS = {
    "requirements.lock": [],
    "requirements-dev.lock": ["--group", "dev", "--constraints", "requirements.lock"],
}


def compile_locks(directory: Path) -> None:
    for name, options in LOCKS.items():
        subprocess.run(
            [sys.executable, "-m", "uv", "pip", "compile", "pyproject.toml", "--universal", "--python-version",
             "3.13", *options, "--output-file", name, "--quiet", "--custom-compile-command", COMMAND],
            cwd=directory, check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Обновить файлы зависимостей backend по pyproject.toml")
    parser.add_argument("--check", action="store_true", help="не записывать, а проверить актуальность файлов")
    args = parser.parse_args()
    if not args.check:
        compile_locks(BACKEND)
        print("Записаны backend/requirements.lock и backend/requirements-dev.lock.")
        return
    # Existing pins are kept as preferences, so only a change of pyproject.toml alters the result.
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        for name in ("pyproject.toml", *LOCKS):
            shutil.copy(BACKEND / name, work / name)
        compile_locks(work)
        stale = [name for name in LOCKS if (work / name).read_text() != (BACKEND / name).read_text()]
    if stale:
        raise SystemExit(f"{', '.join('backend/' + name for name in stale)} устарел: выполните {COMMAND}")
    print("Файлы зависимостей backend актуальны.")


if __name__ == "__main__":
    main()
