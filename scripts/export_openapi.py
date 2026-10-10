#!/usr/bin/env python3
"""Write the OpenAPI document of the current code to docs/openapi.json; --check fails if it is stale."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "openapi.json"


def render() -> str:
    # Building the schema needs no database; the engine is created lazily and never connects.
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузить OpenAPI-спецификацию в docs/openapi.json")
    parser.add_argument("--check", action="store_true", help="не записывать, а проверить актуальность файла")
    args = parser.parse_args()
    document = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != document:
            raise SystemExit("docs/openapi.json устарел: выполните python3 scripts/export_openapi.py")
        print("docs/openapi.json актуален.")
        return
    TARGET.write_text(document, encoding="utf-8")
    print(f"Записано {TARGET.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
