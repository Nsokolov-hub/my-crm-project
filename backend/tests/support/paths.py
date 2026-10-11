from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
MIGRATIONS_DIR = BACKEND_DIR / "alembic" / "versions"
