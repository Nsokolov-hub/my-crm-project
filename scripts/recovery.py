#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


def get_env():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_vars = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip()
    return env_vars


def get_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_data_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_extract(tar: tarfile.TarFile, target_dir: Path):
    target_dir = target_dir.resolve()
    for member in tar.getmembers():
        # Prevent absolute paths or path traversal (Tar Slip vulnerability)
        dest = (target_dir / member.name).resolve()
        if not (dest == target_dir or target_dir in dest.parents):
            raise ValueError(f"Security error: archive member '{member.name}' attempts path traversal")
    if hasattr(tarfile, "data_filter"):
        tar.extractall(path=target_dir, filter="data")
    else:
        tar.extractall(path=target_dir)


def detect_mode(explicit_mode: str, db_url: str | None) -> str:
    if explicit_mode and explicit_mode != "auto":
        return explicit_mode

    # Check if host pg_dump is available
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return "host"

    # Fallback to docker compose
    if shutil.which("docker"):
        res = subprocess.run(
            ["docker", "compose", "ps", "-q", "db"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return "docker"

    # Default to host if tools might be installed or docker not running
    if shutil.which("docker"):
        return "docker"
    return "host"


def extract_db_args(db_url: str):
    parsed = urllib.parse.urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
    args = []
    if parsed.hostname:
        args.extend(["-h", parsed.hostname])
    if parsed.port:
        args.extend(["-p", str(parsed.port)])
    if parsed.username:
        args.extend(["-U", parsed.username])
    return args, parsed.path.lstrip("/"), parsed.username or "crm", parsed.password or ""


def apply_retention(output_dir: Path, keep_count: int):
    if keep_count <= 0:
        return
    backups = sorted(output_dir.glob("backup_*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(backups) > keep_count:
        for old in backups[keep_count:]:
            print(f"Retention policy: removing old backup {old.name}")
            old.unlink()


def backup(args):
    env = get_env()
    db_url = args.database_url or env.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not provided or found in .env")

    storage_dir = args.storage_dir or env.get("STORAGE_DIR", ".runtime/files")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mode = detect_mode(args.mode, db_url)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    archive_path = out_dir / f"backup_{backup_id}.tar.gz"

    db_args, db_name, db_user, db_pass = extract_db_args(db_url)
    container_db_user = env.get("POSTGRES_USER", db_user)
    container_db_name = env.get("POSTGRES_DB", db_name)

    print(f"[{backup_id}] Starting backup in mode: {mode}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_dump = tmp / "db.dump"
        files_tar = tmp / "files.tar.gz"

        # 1. Database dump
        print(f"[{backup_id}] Dumping database ({db_name})...")
        if mode == "docker":
            # Stream directly from container stdout to local file
            cmd = [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                container_db_user,
                "-d",
                container_db_name,
                "-Fc",
            ]
            try:
                with open(db_dump, "wb") as out_f:
                    subprocess.run(cmd, stdout=out_f, check=True, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Docker pg_dump failed: {e.stderr.decode('utf-8', errors='replace')}")
        else:
            # Host mode execution
            pg_dump_bin = shutil.which("pg_dump")
            if not pg_dump_bin:
                sys.exit("ERROR: pg_dump not found in PATH for host backup mode.")
            cmd = [pg_dump_bin] + db_args + ["-Fc", "-f", str(db_dump), db_name]
            run_env = os.environ.copy()
            if db_pass:
                run_env["PGPASSWORD"] = db_pass
            try:
                subprocess.run(cmd, env=run_env, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Host pg_dump failed: {e.stderr.decode('utf-8', errors='replace')}")

        if not db_dump.exists() or db_dump.stat().st_size == 0:
            sys.exit("ERROR: Database dump produced an empty file.")

        # 2. File storage archive
        print(f"[{backup_id}] Archiving files...")
        file_list = []

        if mode == "docker" and args.compose_storage:
            # Check container storage directory exists
            check_cmd = ["docker", "compose", "exec", "-T", "api", "test", "-d", "/data/files"]
            check_res = subprocess.run(check_cmd)
            if check_res.returncode != 0:
                sys.exit("ERROR: Expected Compose storage directory /data/files not found in container api.")

            tar_cmd = ["docker", "compose", "exec", "-T", "api", "tar", "-czf", "-", "-C", "/data/files", "."]
            try:
                with open(files_tar, "wb") as out_f:
                    subprocess.run(tar_cmd, stdout=out_f, check=True, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Failed to archive files from Docker container: {e.stderr.decode('utf-8', errors='replace')}")

            # Inspect archive to populate manifest
            with tarfile.open(files_tar, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        f = tar.extractfile(member)
                        content = f.read() if f else b""
                        file_list.append({
                            "path": member.name.lstrip("./"),
                            "size": member.size,
                            "sha256": get_data_hash(content),
                        })
        else:
            storage_path = Path(storage_dir)
            if not storage_path.exists():
                sys.exit(f"ERROR: File storage directory '{storage_dir}' does not exist. Empty archives are forbidden.")

            with tarfile.open(files_tar, "w:gz") as tar:
                for p in sorted(storage_path.rglob("*")):
                    if p.is_file():
                        rel = str(p.relative_to(storage_path))
                        file_list.append({
                            "path": rel,
                            "size": p.stat().st_size,
                            "sha256": get_hash(p),
                        })
                tar.add(storage_dir, arcname=".")

        # 3. Create manifest
        manifest = {
            "manifest_version": "2.0",
            "backup_id": backup_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": db_name,
            "mode": mode,
            "db_hash": get_hash(db_dump),
            "db_size": db_dump.stat().st_size,
            "files_hash": get_hash(files_tar),
            "files_size": files_tar.stat().st_size,
            "files_count": len(file_list),
            "files": file_list,
        }

        manifest_path = tmp / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # 4. Pack final backup archive
        print(f"[{backup_id}] Creating final backup archive {archive_path}...")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(db_dump, arcname="db.dump")
            tar.add(files_tar, arcname="files.tar.gz")
            tar.add(manifest_path, arcname="manifest.json")

    # 5. Apply retention policy
    if args.retention:
        apply_retention(out_dir, args.retention)

    archive_sha = get_hash(archive_path)
    print(f"[{backup_id}] Backup completed successfully!")
    print(f"Archive: {archive_path}")
    print(f"Archive SHA-256: {archive_sha}")
    print(f"Database dump size: {manifest['db_size']} bytes")
    print(f"Files archived: {manifest['files_count']} ({manifest['files_size']} bytes)")


def restore(args):
    if not args.force:
        sys.exit("ERROR: Restore requires --force to acknowledge overwriting current data.")

    archive_path = Path(args.input_archive)
    if not archive_path.exists():
        sys.exit(f"ERROR: Archive {archive_path} not found.")

    env = get_env()
    db_url = args.database_url or env.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not provided or found in .env")

    storage_dir = args.storage_dir or env.get("STORAGE_DIR", ".runtime/files")
    mode = detect_mode(args.mode, db_url)
    db_args, db_name, db_user, db_pass = extract_db_args(db_url)
    container_db_user = env.get("POSTGRES_USER", db_user)
    container_db_name = env.get("POSTGRES_DB", db_name)

    start_time = time.monotonic()
    print(f"Starting restore from {archive_path} in mode: {mode}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Safe extraction of archive
        print("Extracting backup archive...")
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_extract(tar, tmp)

        manifest_path = tmp / "manifest.json"
        db_dump = tmp / "db.dump"
        files_tar = tmp / "files.tar.gz"

        if not manifest_path.exists():
            sys.exit("ERROR: manifest.json not found in archive.")
        if not db_dump.exists():
            sys.exit("ERROR: db.dump not found in archive.")
        if not files_tar.exists():
            sys.exit("ERROR: files.tar.gz not found in archive.")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        # 2. Checksum validation
        print("Validating archive checksums against manifest...")
        calculated_db_hash = get_hash(db_dump)
        if calculated_db_hash != manifest.get("db_hash"):
            sys.exit(f"ERROR: Database dump hash mismatch! Expected {manifest.get('db_hash')}, got {calculated_db_hash}")

        calculated_files_hash = get_hash(files_tar)
        if calculated_files_hash != manifest.get("files_hash"):
            sys.exit(f"ERROR: Files archive hash mismatch! Expected {manifest.get('files_hash')}, got {calculated_files_hash}")

        print("Checksum verification PASSED.")

        # 3. Database restore
        print(f"Restoring database ({db_name})...")
        if mode == "docker":
            restore_cmd = [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "pg_restore",
                "-U",
                container_db_user,
                "-d",
                container_db_name,
                "--clean",
                "--if-exists",
                "-1",
            ]
            try:
                with open(db_dump, "rb") as in_f:
                    subprocess.run(restore_cmd, stdin=in_f, check=True, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Docker pg_restore failed: {e.stderr.decode('utf-8', errors='replace')}")
        else:
            pg_restore_bin = shutil.which("pg_restore")
            if not pg_restore_bin:
                sys.exit("ERROR: pg_restore not found in PATH for host restore mode.")
            restore_cmd = [pg_restore_bin] + db_args + ["--clean", "--if-exists", "-1", "-d", db_name, str(db_dump)]
            run_env = os.environ.copy()
            if db_pass:
                run_env["PGPASSWORD"] = db_pass
            try:
                subprocess.run(restore_cmd, env=run_env, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Host pg_restore failed: {e.stderr.decode('utf-8', errors='replace')}")

        # 4. Files restore
        print("Restoring file storage...")
        if mode == "docker" and args.compose_storage:
            clean_cmd = ["docker", "compose", "exec", "-T", "api", "sh", "-c", "rm -rf /data/files/*"]
            subprocess.run(clean_cmd, check=True)
            tar_cmd = ["docker", "compose", "exec", "-T", "api", "tar", "-xzf", "-", "-C", "/data/files"]
            try:
                with open(files_tar, "rb") as in_f:
                    subprocess.run(tar_cmd, stdin=in_f, check=True, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                sys.exit(f"ERROR: Failed to restore files to Docker container: {e.stderr.decode('utf-8', errors='replace')}")
        else:
            storage_path = Path(storage_dir)
            if storage_path.exists():
                shutil.rmtree(storage_path)
            storage_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(files_tar, "r:gz") as tar:
                safe_extract(tar, storage_path)

            # 5. Verify restored files against manifest
            if "files" in manifest and manifest["files"]:
                print(f"Verifying {len(manifest['files'])} restored files against manifest...")
                for item in manifest["files"]:
                    file_p = storage_path / item["path"]
                    if not file_p.exists():
                        sys.exit(f"ERROR: Restored file missing: {item['path']}")
                    if get_hash(file_p) != item["sha256"]:
                        sys.exit(f"ERROR: Restored file hash mismatch: {item['path']}")
                print(f"Integrity verification PASSED: all {len(manifest['files'])} files verified.")

    elapsed = time.monotonic() - start_time
    print(f"Restore completed successfully in {elapsed:.2f}s (RTO).")


def main():
    parser = argparse.ArgumentParser(description="Утилита резервного копирования и восстановления (R12/R14)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Создать резервную копию")
    backup_parser.add_argument("--output-dir", default="backups", help="Директория для архива")
    backup_parser.add_argument("--database-url", help="URL базы данных (по умолчанию из .env)")
    backup_parser.add_argument("--storage-dir", help="Директория файлов (по умолчанию из .env)")
    backup_parser.add_argument("--mode", choices=["auto", "host", "docker"], default="auto", help="Способ выполнения (auto/host/docker)")
    backup_parser.add_argument("--compose-storage", action="store_true", help="Копировать хранилище файлов из контейнера api (/data/files)")
    backup_parser.add_argument("--retention", type=int, default=0, help="Количество сохраняемых последних копий (ротация)")

    restore_parser = subparsers.add_parser("restore", help="Восстановить из резервной копии")
    restore_parser.add_argument("input_archive", help="Путь к архиву .tar.gz")
    restore_parser.add_argument("--database-url", help="URL базы данных (по умолчанию из .env)")
    restore_parser.add_argument("--storage-dir", help="Директория файлов (по умолчанию из .env)")
    restore_parser.add_argument("--mode", choices=["auto", "host", "docker"], default="auto", help="Способ выполнения (auto/host/docker)")
    restore_parser.add_argument("--compose-storage", action="store_true", help="Восстанавливать хранилище файлов в контейнер api (/data/files)")
    restore_parser.add_argument("--force", action="store_true", help="Обязательный флаг для подтверждения перезаписи")

    args = parser.parse_args()
    if args.command == "backup":
        backup(args)
    elif args.command == "restore":
        restore(args)


if __name__ == "__main__":
    main()
