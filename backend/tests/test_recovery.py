"""Tests for R12/R14 Backup, Restore, Role Separation, and Integrity."""

import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.init_roles import init_roles


def get_hash(filepath):
    import hashlib
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_init_roles_idempotency_and_no_ddl():
    """Verify role initialization creates roles idempotently and restricts DDL."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Requires PostgreSQL database")

    # Run init_roles twice to verify idempotency on existing volume
    init_roles()
    init_roles()

    # Connect with restricted role crm_api and verify DDL is blocked
    import psycopg
    conn = psycopg.connect(
        f"postgresql://crm_api:{settings.crm_api_password}@127.0.0.1:54329/{settings.database_url.split('/')[-1]}"
    )
    with conn.cursor() as cur:
        # SELECT should work
        cur.execute("SELECT 1;")
        assert cur.fetchone()[0] == 1

        # DDL (DROP TABLE) on application tables must be denied
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DROP TABLE users CASCADE;")
    conn.close()

    # Connect with restricted role crm_backup and verify write (INSERT) is blocked
    bconn = psycopg.connect(
        f"postgresql://crm_backup:{settings.crm_backup_password}@127.0.0.1:54329/{settings.database_url.split('/')[-1]}"
    )
    with bconn.cursor() as cur:
        # SELECT should work
        cur.execute("SELECT count(*) FROM users;")
        assert cur.fetchone()[0] >= 0

        # INSERT must be denied
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO users (id, name, email, password_hash) VALUES ('bad', 'bad', 'bad@test.com', 'x');")
    bconn.close()


def test_backup_fails_if_storage_dir_missing(tmp_path):
    """Verify that backup raises an error if storage directory is missing (no silent empty archive)."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "recovery.py"
    missing_storage = tmp_path / "nonexistent_storage"

    res = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "backup",
            "--output-dir",
            str(tmp_path / "backups"),
            "--storage-dir",
            str(missing_storage),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "does not exist. Empty archives are forbidden" in res.stderr or "does not exist" in res.stderr


def test_restore_requires_force_flag(tmp_path):
    """Verify that restore requires explicit --force flag."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "recovery.py"
    dummy_archive = tmp_path / "backup.tar.gz"
    dummy_archive.touch()

    res = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "restore",
            str(dummy_archive),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "--force" in res.stderr


def test_restore_detects_tampered_dump_and_aborts(tmp_path):
    """Verify that checksum mismatch in db.dump or files.tar.gz immediately aborts restore."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "recovery.py"

    # Create storage with a file
    storage = tmp_path / "files"
    storage.mkdir(parents=True)
    test_doc = storage / "doc.txt"
    test_doc.write_text("Hello, world!")

    # Perform a backup
    out_dir = tmp_path / "backups"
    res = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "backup",
            "--output-dir",
            str(out_dir),
            "--storage-dir",
            str(storage),
            "--mode",
            "docker",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    backup_file = next(out_dir.glob("backup_*.tar.gz"))

    # Tamper with the archive
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with tarfile.open(backup_file, "r:gz") as tar:
        tar.extractall(extract_dir)

    # Corrupt db.dump
    with open(extract_dir / "db.dump", "ab") as f:
        f.write(b"CORRUPTED_BYTES")

    tampered_archive = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as tar:
        tar.add(extract_dir / "db.dump", arcname="db.dump")
        tar.add(extract_dir / "files.tar.gz", arcname="files.tar.gz")
        tar.add(extract_dir / "manifest.json", arcname="manifest.json")

    # Attempt restore -> must fail with checksum mismatch
    res_tamper = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "restore",
            str(tampered_archive),
            "--storage-dir",
            str(storage),
            "--mode",
            "docker",
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert res_tamper.returncode != 0
    assert "hash mismatch" in res_tamper.stderr


def test_full_backup_and_restore_cycle_with_files(tmp_path):
    """Complete E2E cycle: backup -> disaster simulation -> restore -> verify data & file integrity."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "recovery.py"

    storage = tmp_path / "files"
    storage.mkdir(parents=True)
    doc1 = storage / "contract.pdf"
    doc1.write_bytes(b"%PDF-1.4 test contract content %%EOF")
    doc2 = storage / "sheet.xlsx"
    doc2.write_bytes(b"PK\x03\x04 test sheet content")

    hash1 = get_hash(doc1)
    hash2 = get_hash(doc2)

    out_dir = tmp_path / "backups"

    # Step 1: Backup
    res_b = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "backup",
            "--output-dir",
            str(out_dir),
            "--storage-dir",
            str(storage),
            "--mode",
            "docker",
            "--retention",
            "3",
        ],
        capture_output=True,
        text=True,
    )
    assert res_b.returncode == 0, res_b.stderr
    backup_file = next(out_dir.glob("backup_*.tar.gz"))

    # Step 2: Disaster simulation - wipe storage
    shutil.rmtree(storage)
    assert not storage.exists()

    # Step 3: Restore
    t1 = time.monotonic()
    res_r = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "restore",
            str(backup_file),
            "--storage-dir",
            str(storage),
            "--mode",
            "docker",
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert res_r.returncode == 0, res_r.stderr
    rto = time.monotonic() - t1

    # Step 4: Verify restored files and checksums
    assert storage.exists()
    assert (storage / "contract.pdf").exists()
    assert (storage / "sheet.xlsx").exists()
    assert get_hash(storage / "contract.pdf") == hash1
    assert get_hash(storage / "sheet.xlsx") == hash2

    # RTO should be within threshold (< 10 seconds)
    assert rto < 10.0
