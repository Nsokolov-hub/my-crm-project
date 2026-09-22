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
import urllib.parse
from datetime import datetime
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
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def find_executable(name, docker_fallback=False):
    exe = shutil.which(name)
    if exe:
        return [exe]
    if docker_fallback:
        # Fallback to docker compose if available
        if shutil.which("docker"):
            return ["docker", "compose", "exec", "-T", "db", name]
    return None


def extract_db_args(db_url):
    parsed = urllib.parse.urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
    args = []
    if parsed.hostname:
        args.extend(["-h", parsed.hostname])
    if parsed.port:
        args.extend(["-p", str(parsed.port)])
    if parsed.username:
        args.extend(["-U", parsed.username])
    return args, parsed.path.lstrip("/")


def backup(args):
    env = get_env()
    db_url = args.database_url or env.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not provided or found in .env")

    storage_dir = args.storage_dir or env.get("STORAGE_DIR", ".runtime/files")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    backup_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    archive_path = out_dir / f"backup_{backup_id}.tar.gz"

    pg_dump_cmd = find_executable("pg_dump", docker_fallback=True)
    if not pg_dump_cmd:
        sys.exit("ERROR: pg_dump not found in PATH and docker fallback failed.")

    db_args, db_name = extract_db_args(db_url)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_dump = tmp / "db.dump"
        
        print(f"[{backup_id}] Dumping database...")
        cmd = pg_dump_cmd + db_args + ["-Fc", "-f", str(db_dump), db_name]
        
        # We need to set PGPASSWORD if running locally
        run_env = os.environ.copy()
        parsed = urllib.parse.urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
        if parsed.password:
            run_env["PGPASSWORD"] = parsed.password
            
        # If using docker compose exec, we can't easily pass PGPASSWORD, but docker compose uses the env vars internally or doesn't need it if we're execing as postgres user, but wait, pg_dump inside container works.
        try:
            subprocess.run(cmd, env=run_env, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            sys.exit(f"Database dump failed: {e.stderr.decode()}")

        files_tar = tmp / "files.tar.gz"
        print(f"[{backup_id}] Archiving files from {storage_dir}...")
        with tarfile.open(files_tar, "w:gz") as tar:
            if Path(storage_dir).exists():
                tar.add(storage_dir, arcname=".")

        manifest = {
            "backup_id": backup_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "db_hash": get_hash(db_dump),
            "files_hash": get_hash(files_tar),
        }
        
        manifest_path = tmp / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
            
        print(f"[{backup_id}] Creating final archive {archive_path}...")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(db_dump, arcname="db.dump")
            tar.add(files_tar, arcname="files.tar.gz")
            tar.add(manifest_path, arcname="manifest.json")
            
    print(f"Backup completed successfully: {archive_path}")
    print(f"SHA-256: {get_hash(archive_path)}")


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
    
    pg_restore_cmd = find_executable("pg_restore", docker_fallback=True)
    if not pg_restore_cmd:
        sys.exit("ERROR: pg_restore not found in PATH and docker fallback failed.")
        
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        print(f"Extracting {archive_path}...")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=tmp)
            
        manifest_path = tmp / "manifest.json"
        if not manifest_path.exists():
            sys.exit("ERROR: manifest.json not found in archive.")
            
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            
        db_dump = tmp / "db.dump"
        files_tar = tmp / "files.tar.gz"
        
        print("Validating checksums...")
        if get_hash(db_dump) != manifest["db_hash"]:
            sys.exit("ERROR: Database dump hash mismatch! Archive corrupted.")
        if get_hash(files_tar) != manifest["files_hash"]:
            sys.exit("ERROR: Files archive hash mismatch! Archive corrupted.")
            
        db_args, db_name = extract_db_args(db_url)
        print(f"Restoring database {db_name}...")
        cmd = pg_restore_cmd + db_args + ["--clean", "--if-exists", "-1", "-d", db_name, str(db_dump)]
        
        run_env = os.environ.copy()
        parsed = urllib.parse.urlparse(db_url.replace("postgresql+psycopg://", "postgresql://"))
        if parsed.password:
            run_env["PGPASSWORD"] = parsed.password
            
        try:
            subprocess.run(cmd, env=run_env, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            sys.exit(f"Database restore failed: {e.stderr.decode()}")
            
        print(f"Restoring files to {storage_dir}...")
        storage_path = Path(storage_dir)
        if storage_path.exists():
            shutil.rmtree(storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)
        
        with tarfile.open(files_tar, "r:gz") as tar:
            tar.extractall(path=storage_path)
            
    print("Restore completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Утилита резервного копирования и восстановления")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    backup_parser = subparsers.add_parser("backup", help="Создать резервную копию")
    backup_parser.add_argument("--output-dir", default="backups", help="Директория для архива")
    backup_parser.add_argument("--database-url", help="URL базы данных (по умолчанию из .env)")
    backup_parser.add_argument("--storage-dir", help="Директория файлов (по умолчанию из .env)")
    
    restore_parser = subparsers.add_parser("restore", help="Восстановить из резервной копии")
    restore_parser.add_argument("input_archive", help="Путь к архиву .tar.gz")
    restore_parser.add_argument("--database-url", help="URL базы данных (по умолчанию из .env)")
    restore_parser.add_argument("--storage-dir", help="Директория файлов (по умолчанию из .env)")
    restore_parser.add_argument("--force", action="store_true", help="Обязательный флаг для перезаписи текущих данных")
    
    args = parser.parse_args()
    if args.command == "backup":
        backup(args)
    elif args.command == "restore":
        restore(args)


if __name__ == "__main__":
    main()
