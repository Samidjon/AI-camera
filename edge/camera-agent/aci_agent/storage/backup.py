"""Local backups of the agent databases.

``create_backup`` copies ``agent.db`` (cameras, rules, device identity, server link) and
``events.db`` with the SQLite online backup API — consistent while the agent keeps writing — into
``<data_dir>/backups/<UTC timestamp>/``, verifies each copy with ``PRAGMA integrity_check`` and
keeps the newest ``keep`` backups.

The encryption key (``secret.key``) is deliberately **not** copied next to the databases: a backup
alone can't decrypt camera passwords or the device credential. Back the key up separately
(see docs/EDGE_AGENT_GUIDE.md → Backups).
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from aci_agent.storage.database import Database


class BackupError(Exception):
    pass


def _verify(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise BackupError(f"Backup {path.name} failed the integrity check")


def create_backup(databases: dict[str, Database], backups_dir: Path, keep: int = 7) -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = Path(backups_dir) / stamp
    target.mkdir(parents=True, exist_ok=False)
    files = {}
    try:
        for name, db in databases.items():
            dest = target / f"{name}.db"
            db.backup_to(dest)
            _verify(dest)
            files[name] = dest.stat().st_size
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    removed = prune_backups(backups_dir, keep)
    return {"id": stamp, "path": str(target), "files": files, "removed": removed}


def list_backups(backups_dir: Path) -> list[dict]:
    root = Path(backups_dir)
    if not root.exists():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        files = {f.stem: f.stat().st_size for f in d.glob("*.db")}
        out.append({"id": d.name, "path": str(d), "files": files, "size_bytes": sum(files.values())})
    return out


def prune_backups(backups_dir: Path, keep: int) -> list[str]:
    dirs = sorted((p for p in Path(backups_dir).iterdir() if p.is_dir()), reverse=True)
    removed = []
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def restore_backup(backup_dir: Path, data_dir: Path) -> list[str]:
    """Restore databases from a backup directory. The agent **must be stopped** (CLI only).

    Current files are kept as ``*.db.before-restore`` so a restore can be undone.
    """
    restored = []
    for src in sorted(Path(backup_dir).glob("*.db")):
        _verify(src)
        dest = Path(data_dir) / src.name
        if dest.exists():
            shutil.copy2(dest, dest.with_name(dest.name + ".before-restore"))
        for suffix in ("-wal", "-shm"):
            Path(str(dest) + suffix).unlink(missing_ok=True)
        shutil.copy2(src, dest)
        restored.append(src.name)
    if not restored:
        raise BackupError("No database files in that backup")
    return restored
