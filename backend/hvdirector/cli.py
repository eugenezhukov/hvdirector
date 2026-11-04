"""hvdirector-manage: administrative command line."""

from __future__ import annotations

import argparse
import getpass
import socket
import sys
from pathlib import Path


def _alembic_config():  # noqa: ANN202
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return cfg


def cmd_gen_key(_: argparse.Namespace) -> int:
    from .security import generate_key

    print(generate_key())
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command

    command.upgrade(_alembic_config(), args.revision)
    print("Database schema is up to date")
    return 0


def cmd_makemigration(args: argparse.Namespace) -> int:
    from alembic import command

    command.revision(_alembic_config(), message=args.message, autogenerate=True)
    return 0


def _read_password(given: str | None) -> str:
    if given:
        return given
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Repeat password: ")
        if p1 == p2 and p1:
            return p1
        print("Passwords do not match, try again", file=sys.stderr)


def cmd_create_admin(args: argparse.Namespace) -> int:
    from .db import session_scope
    from .errors import AppError
    from .services import users as user_svc

    password = _read_password(args.password)
    try:
        with session_scope() as db:
            user = user_svc.create_user(db, None, {"username": args.username, "password": password,
                                                   "full_name": args.full_name or "Provider administrator"})
            print(f"Provider administrator '{user.username}' created")
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    return 0


def cmd_bootstrap_admin(args: argparse.Namespace) -> int:
    """Create the first provider administrator if none exists (used by the installer)."""
    import secrets

    from sqlalchemy import func, select

    from .db import session_scope
    from .models import User
    from .services import users as user_svc

    with session_scope() as db:
        if db.scalar(select(func.count(User.id)).where(User.tenant_id.is_(None))):
            print("provider administrator already exists")
            return 0
        password = args.password or (secrets.token_urlsafe(9) + "-A1")
        user_svc.create_user(db, None, {"username": args.username, "password": password,
                                        "full_name": "Provider administrator"})
        print(f"CREATED {args.username} {password}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from .db import session_scope
    from .models import Tenant, User
    from .services import users as user_svc

    password = _read_password(args.password)
    with session_scope() as db:
        q = select(User).where(func.lower(User.username) == args.username.lower())
        if args.org:
            tenant = db.scalar(select(Tenant).where(Tenant.slug == args.org.lower()))
            if tenant is None:
                print("Unknown organization", file=sys.stderr)
                return 1
            q = q.where(User.tenant_id == tenant.id)
        else:
            q = q.where(User.tenant_id.is_(None))
        user = db.scalar(q)
        if user is None:
            print("User not found", file=sys.stderr)
            return 1
        user_svc.update_user(db, user, {"password": password, "unlock": True, "is_active": True})
        print("Password updated")
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    from sqlalchemy import text

    from .config import get_config
    from .db import SessionLocal
    from .services.settings import get_setting

    cfg = get_config()
    print(f"Database:     {cfg.database_url.split('@')[-1]}")
    print(f"Hyper-V driver: {cfg.hyperv_driver}")
    ok = True
    db = SessionLocal()
    try:
        db.execute(text("select 1"))
        print("PostgreSQL:   OK")
        g = get_setting(db, "guacd")
    except Exception as exc:  # noqa: BLE001
        print(f"PostgreSQL:   FAILED ({exc})")
        return 1
    finally:
        db.close()
    try:
        with socket.create_connection((g["host"], int(g["port"])), timeout=5) as s:
            s.sendall(b"6.select,3.rdp;")
            data = s.recv(256).decode("utf-8", "replace")
        print(f"guacd:        {'OK' if data.startswith('4.args') else 'unexpected answer'} ({g['host']}:{g['port']})")
    except OSError as exc:
        ok = False
        print(f"guacd:        FAILED ({exc})")
    return 0 if ok else 2


def cmd_hv_run(args: argparse.Namespace) -> int:
    """Run one of the PowerShell scripts of HV Director on a Hyper-V node and print its JSON result.

    With --cluster the stored (encrypted) WinRM settings of that cluster are used; otherwise the
    connection is given on the command line (password from HVD_HV_PASSWORD or a prompt).
    """
    import json
    import os

    from sqlalchemy import select

    from .hyperv import Endpoint, HyperVError
    from .hyperv.psrp import SCRIPTS_DIR, PsrpDriver

    script = args.script.removesuffix(".ps1")
    if not (SCRIPTS_DIR / f"{script}.ps1").exists() or script.startswith("_"):
        names = sorted(p.stem for p in SCRIPTS_DIR.glob("*.ps1") if not p.stem.startswith("_"))
        print(f"Unknown script {script}. Available: {', '.join(names)}", file=sys.stderr)
        return 2
    try:
        script_args = json.loads(args.args) if args.args else {}
    except ValueError as exc:
        print(f"--args must be JSON: {exc}", file=sys.stderr)
        return 2
    if args.cluster:
        from .db import SessionLocal
        from .models import Cluster
        from .services.inventory import endpoint_for

        db = SessionLocal()
        try:
            cluster = db.scalar(select(Cluster).where(Cluster.name == args.cluster))
            if cluster is None:
                print(f"Cluster {args.cluster!r} not found", file=sys.stderr)
                return 1
            host = None
            if args.host:
                host = next((h for h in cluster.hosts if args.host.lower() in (h.name.lower(), (h.address or "").lower())),
                            None)
                if host is None:
                    print(f"Node {args.host!r} is not part of {cluster.name}", file=sys.stderr)
                    return 1
            ep = endpoint_for(cluster, host)
        finally:
            db.close()
    else:
        if not args.address or not args.username:
            print("Give --cluster NAME, or --address and --username", file=sys.stderr)
            return 2
        password = os.environ.get("HVD_HV_PASSWORD") or getpass.getpass(f"Password for {args.username}: ")
        ep = Endpoint(address=args.address, username=args.username, password=password, transport=args.transport,
                      port=args.port or (5986 if args.transport == "https" else 5985), auth=args.auth,
                      cert_validation=args.cert_validation)
    try:
        result = PsrpDriver(operation_timeout=args.timeout, read_timeout=args.timeout + 10).run(ep, script, script_args)
    except HyperVError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "host": ep.display, "error": str(exc)}, ensure_ascii=False,
                         indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_fake(args: argparse.Namespace) -> int:
    from .config import get_config
    from .hyperv.fake import FakeDriver

    drv = FakeDriver(get_config().fake_state_file, delay=0)
    if args.fake_cmd == "add-node":
        drv.sim_add_node(args.cluster, args.name, args.address)
    elif args.fake_cmd == "remove-node":
        drv.sim_remove_node(args.cluster, args.name)
    elif args.fake_cmd == "node-state":
        drv.sim_set_node_state(args.name, args.state)
    elif args.fake_cmd == "migrate":
        drv.sim_migrate(args.vm, args.host)
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hvdirector-manage", description="HV Director administration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gen-key", help="generate a master key for HVD_SECRET_KEY").set_defaults(fn=cmd_gen_key)

    p = sub.add_parser("migrate", help="create/upgrade the database schema")
    p.add_argument("revision", nargs="?", default="head")
    p.set_defaults(fn=cmd_migrate)

    p = sub.add_parser("makemigration", help="(developers) autogenerate a migration")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(fn=cmd_makemigration)

    p = sub.add_parser("create-admin", help="create a provider administrator")
    p.add_argument("--username", default="admin")
    p.add_argument("--password")
    p.add_argument("--full-name")
    p.set_defaults(fn=cmd_create_admin)

    p = sub.add_parser("bootstrap-admin", help="create the first provider administrator if none exists")
    p.add_argument("--username", default="admin")
    p.add_argument("--password")
    p.set_defaults(fn=cmd_bootstrap_admin)

    p = sub.add_parser("reset-password", help="reset a user's password (and unlock)")
    p.add_argument("--username", required=True)
    p.add_argument("--org", help="organization code for tenant users")
    p.add_argument("--password")
    p.set_defaults(fn=cmd_reset_password)

    sub.add_parser("check", help="check database and guacd connectivity").set_defaults(fn=cmd_check)

    p = sub.add_parser("hv-run", help="(debugging) run an HV Director PowerShell script on a Hyper-V node")
    p.add_argument("script", help="script name, e.g. ping, host_info, list_vms, vm_info, vm_metrics")
    p.add_argument("--args", help='JSON arguments of the script, e.g. \'{"id": "<VM GUID>"}\'')
    p.add_argument("--cluster", help="use the stored connection settings of this cluster")
    p.add_argument("--host", help="node name or address within --cluster (default: the cluster seed address)")
    p.add_argument("--address", help="node address (without --cluster)")
    p.add_argument("--username", help="DOMAIN\\user (without --cluster)")
    p.add_argument("--auth", default="ntlm", choices=["ntlm", "negotiate", "kerberos", "credssp", "basic"])
    p.add_argument("--transport", default="http", choices=["http", "https"])
    p.add_argument("--port", type=int)
    p.add_argument("--cert-validation", action="store_true")
    p.add_argument("--timeout", type=int, default=60, help="WinRM operation timeout, s")
    p.set_defaults(fn=cmd_hv_run)

    p = sub.add_parser("fake", help="control the Hyper-V simulator (HVD_HYPERV_DRIVER=fake)")
    fsub = p.add_subparsers(dest="fake_cmd", required=True)
    x = fsub.add_parser("add-node")
    x.add_argument("cluster")
    x.add_argument("name")
    x.add_argument("address")
    x = fsub.add_parser("remove-node")
    x.add_argument("cluster")
    x.add_argument("name")
    x = fsub.add_parser("node-state")
    x.add_argument("name")
    x.add_argument("state")
    x = fsub.add_parser("migrate")
    x.add_argument("vm")
    x.add_argument("host")
    p.set_defaults(fn=cmd_fake)

    args = parser.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
