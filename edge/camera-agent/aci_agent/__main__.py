"""Edge agent command line.

python -m aci_agent                 run the agent (same as ``run``)
python -m aci_agent run
python -m aci_agent configure       interactive configuration wizard → env file
python -m aci_agent status          local status (works while the agent is stopped)
python -m aci_agent backup          consistent backup of the databases (safe while running)
python -m aci_agent backups         list backups
python -m aci_agent restore ID      restore a backup (agent must be stopped)
python -m aci_agent register --server URL [--token-file FILE]
python -m aci_agent unlink
python -m aci_agent version
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
from pathlib import Path

from aci_agent import __version__


def _settings():
    from aci_agent.configuration.settings import AgentSettings

    return AgentSettings()


def cmd_run(_: argparse.Namespace) -> int:
    import uvicorn

    from aci_agent.api.app import create_app

    settings = _settings()
    app = create_app(settings)
    # Access log off: preview URLs carry short-lived stream tokens in the query string.
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)
    return 0


def _open_runtime(settings):
    from aci_agent.agent.device import DeviceIdentity
    from aci_agent.events.store import EventStore, EventStoreSettings
    from aci_agent.networking.link import ServerLinkRepository
    from aci_agent.security.crypto import SecretBox
    from aci_agent.storage.database import Database

    db = Database(settings.database_path)
    box = SecretBox.from_settings(settings.secret_key, settings.secret_key_path)
    # initial maintenance far in the future: the CLI must not prune anything
    store = EventStore(settings.events_database_path, EventStoreSettings(initial_maintenance_delay_s=1e9,
                                                                         maintenance_interval_s=1e9))  # fmt: skip
    identity = DeviceIdentity(db, settings.device_name or None)
    return db, box, store, identity, ServerLinkRepository(db, box)


def cmd_status(_: argparse.Namespace) -> int:
    from aci_agent.storage.backup import list_backups

    settings = _settings()
    db, _box, store, identity, links = _open_runtime(settings)
    try:
        link = links.get()
        backups = list_backups(settings.backups_dir)
        out = {
            "agent_version": __version__,
            "data_dir": str(settings.data_dir.resolve()),
            "device_id": identity.device_id,
            "device_name": identity.device_name,
            "database_ok": db.integrity_ok() and store.db.integrity_ok(),
            "events_total": store.stats()["events_total"],
            "sync_queue": store.sync_stats() | {"enabled": bool(link and link.has_credentials and not link.revoked_at)},
            "server_link": link.public() if link else None,
            "auth_configured": settings.auth_configured,
            "latest_backup": backups[0]["id"] if backups else None,
            "agent_running": _agent_running(settings),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        store.close()
        db.close()
    return 0


def _agent_running(settings) -> bool:
    try:
        with socket.create_connection(("127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host,
                                       settings.port), timeout=0.5):  # fmt: skip
            return True
    except OSError:
        return False


def cmd_backup(_: argparse.Namespace) -> int:
    from aci_agent.storage.backup import create_backup

    settings = _settings()
    db, _box, store, _identity, _links = _open_runtime(settings)
    try:
        result = create_backup({"agent": db, "events": store.db}, settings.backups_dir, settings.runtime.backup_keep)
    finally:
        store.close()
        db.close()
    print(json.dumps(result, indent=2))
    return 0


def cmd_backups(_: argparse.Namespace) -> int:
    from aci_agent.storage.backup import list_backups

    print(json.dumps(list_backups(_settings().backups_dir), indent=2))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from aci_agent.storage.backup import restore_backup

    settings = _settings()
    if _agent_running(settings):
        print("The agent is running — stop it first (sudo systemctl stop aci-agent).", file=sys.stderr)
        return 2
    source = settings.backups_dir / args.backup_id
    if not source.is_dir() or source.resolve().parent != settings.backups_dir.resolve():
        print(f"No backup '{args.backup_id}' in {settings.backups_dir}", file=sys.stderr)
        return 2
    restored = restore_backup(source, settings.data_dir)
    print(f"Restored: {', '.join(restored)} (previous files kept as *.before-restore)")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    from aci_agent.synchronization.manager import NetworkManager, RegistrationError

    settings = _settings()
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = getpass.getpass("Enrollment token (input hidden): ").strip()
    db, _box, store, identity, links = _open_runtime(settings)
    manager = NetworkManager(settings.server, identity, links, store)
    try:
        status = manager.register(args.server, token)
        print(json.dumps({"state": status["state"], "link": status["link"]}, indent=2))
        print("Registered. A running agent picks the link up within a minute (or restart it).")
        return 0
    except (RegistrationError, ValueError) as exc:
        print(f"Registration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        manager.shutdown()
        store.close()
        db.close()


def cmd_unlink(_: argparse.Namespace) -> int:
    from aci_agent.synchronization.manager import NetworkManager

    settings = _settings()
    db, _box, store, identity, links = _open_runtime(settings)
    try:
        NetworkManager(settings.server, identity, links, store).unlink()
        print("Unlinked from the central server. Local data is kept.")
    finally:
        store.close()
        db.close()
    return 0


# ---------------------------------------------------------------------------- configuration wizard


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{'***' if secret and default else default}]" if default else ""
    reader = getpass.getpass if secret else input
    value = reader(f"{prompt}{suffix}: ").strip()
    return value or default


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write KEY=VALUE lines with owner-only permissions (secrets inside)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# AI Camera Intelligence — edge agent configuration (generated by `aci_agent configure`)\n")
        for key, value in values.items():
            if value != "":
                fh.write(f"{key}={value}\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cmd_configure(args: argparse.Namespace) -> int:
    from aci_agent.networking.client import validate_server_url

    path = Path(args.output)
    current = _read_env(path)
    print(f"Edge agent configuration → {path}\nPress Enter to keep the value in brackets.\n")
    p = "ACI_AGENT_"
    v = dict(current)
    v[p + "ENVIRONMENT"] = _ask("Environment (production/development)", current.get(p + "ENVIRONMENT", "production"))
    v[p + "HOST"] = _ask("Listen address (127.0.0.1 = this machine only, 0.0.0.0 = LAN)",
                         current.get(p + "HOST", "127.0.0.1"))  # fmt: skip
    v[p + "PORT"] = _ask("Port", current.get(p + "PORT", "8100"))
    v[p + "DATA_DIR"] = _ask("Data directory", current.get(p + "DATA_DIR", "/var/lib/aci-agent"))
    v[p + "DEVICE_NAME"] = _ask("Device name", current.get(p + "DEVICE_NAME", socket.gethostname()))
    origins = _ask("Dashboard URL(s) allowed to call this agent (comma separated)",
                   ",".join(json.loads(current.get(p + "CORS_ORIGINS", '["http://localhost:3000"]'))))  # fmt: skip
    v[p + "CORS_ORIGINS"] = json.dumps([o.strip() for o in origins.split(",") if o.strip()], separators=(",", ":"))
    jwt_secret = _ask("JWT secret shared with the central server (ACI_JWT_SECRET)",
                      current.get(p + "JWT_SECRET", ""), secret=True)  # fmt: skip
    if jwt_secret and len(jwt_secret) < 16:
        print("  ! shorter than 16 characters — the management API will stay disabled")
    v[p + "JWT_SECRET"] = jwt_secret
    server = _ask("Central server URL (empty = standalone, no upload)", current.get(p + "SERVER_URL", ""))
    if server:
        insecure = current.get(p + "SERVER_ALLOW_INSECURE_HTTP", "false").lower() == "true"
        if server.startswith("http://"):
            insecure = _ask("Plain HTTP is insecure. Allow anyway (lab only)? yes/no", "no").lower() == "yes"
        try:
            server = validate_server_url(server, insecure)
        except ValueError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            return 1
        v[p + "SERVER_URL"] = server
        v[p + "SERVER_ALLOW_INSECURE_HTTP"] = "true" if insecure else ""
        token = _ask("Enrollment token from the central server (used once; empty = register later)",
                     current.get(p + "SERVER_ENROLLMENT_TOKEN", ""), secret=True)  # fmt: skip
        v[p + "SERVER_ENROLLMENT_TOKEN"] = token
        v[p + "SERVER_CA_FILE"] = _ask(
            "Custom CA bundle path (empty = system CAs)", current.get(p + "SERVER_CA_FILE", "")
        )
    else:
        for key in ("SERVER_URL", "SERVER_ENROLLMENT_TOKEN", "SERVER_ALLOW_INSECURE_HTTP", "SERVER_CA_FILE"):
            v.pop(p + key, None)
    v.setdefault(p + "LOG_JSON", "true")
    write_env_file(path, v)
    print(f"\nSaved {path} (permissions 600). Restart the agent to apply: sudo systemctl restart aci-agent")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aci_agent", description="AI Camera Intelligence edge agent")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the agent")
    sub.add_parser("status", help="local status")
    sub.add_parser("backup", help="create a backup of the databases")
    sub.add_parser("backups", help="list backups")
    r = sub.add_parser("restore", help="restore a backup (agent stopped)")
    r.add_argument("backup_id")
    reg = sub.add_parser("register", help="register with a central server")
    reg.add_argument("--server", required=True)
    reg.add_argument("--token-file", help="file containing the enrollment token (else prompted)")
    sub.add_parser("unlink", help="forget the central server")
    c = sub.add_parser("configure", help="configuration wizard")
    c.add_argument("--output", default=os.environ.get("ACI_AGENT_ENV_FILE", ".env"))
    sub.add_parser("version")
    args = parser.parse_args(argv)
    commands = {
        None: cmd_run, "run": cmd_run, "status": cmd_status, "backup": cmd_backup, "backups": cmd_backups,
        "restore": cmd_restore, "register": cmd_register, "unlink": cmd_unlink, "configure": cmd_configure,
        "version": lambda _: print(__version__) or 0,
    }  # fmt: skip
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
