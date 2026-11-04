"""VM console: WebSocket tunnel between guacamole-common-js and guacd.

Browser (iframe /console.html) --WS--> /ws/console?ticket=... --TCP--> guacd
guacd --RDP (vmconnect, port 2179, preconnection-blob=VMId)--> Hyper-V node owning the VM.

Credentials for the Hyper-V host never reach the browser: they are read from
the database and injected into the guacd handshake here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from ..db import SessionLocal
from ..guac import INTERNAL_OPCODE, GuacdConnection, GuacError, InstructionParser, encode
from ..hyperv import HyperVError, get_driver
from ..models import VM, ConsoleTicket, utcnow
from ..security import decrypt_secret, hash_token
from ..services.audit import log_event
from ..services.inventory import locate_vm
from ..services.settings import get_setting
from .deps import SESSION_COOKIE, load_principal

log = logging.getLogger(__name__)
router = APIRouter()

# Guacamole status codes
UNAUTHORIZED = "769"     # 0x0301
FORBIDDEN = "771"        # 0x0303
UPSTREAM_ERROR = "515"   # 0x0203
UPSTREAM_NOT_FOUND = "519"  # 0x0207
SERVER_ERROR = "512"     # 0x0200


class ConsoleDenied(Exception):
    def __init__(self, message: str, status: str) -> None:
        super().__init__(message)
        self.status = status


def split_user(username: str) -> tuple[str, str]:
    """'DOMAIN\\user' -> ('user', 'DOMAIN'); 'user@domain' stays a UPN."""
    if "\\" in username:
        domain, user = username.split("\\", 1)
        return user, domain
    return username, ""


def prepare_console(token: str | None, ticket: str | None, ip: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        principal = load_principal(db, token, ip)
        if principal is None:
            raise ConsoleDenied("Session expired, please log in again", UNAUTHORIZED)
        if not ticket:
            raise ConsoleDenied("Missing console ticket", FORBIDDEN)
        row = db.execute(select(ConsoleTicket).where(ConsoleTicket.token_hash == hash_token(ticket))
                         .with_for_update()).scalar_one_or_none()
        if row is None or row.used or row.expires_at < utcnow() or row.user_id != principal.user.id:
            raise ConsoleDenied("Console ticket is invalid or expired", FORBIDDEN)
        row.used = True
        db.commit()
        vm = db.get(VM, row.vm_id)
        if vm is None or vm.tenant_id != principal.tenant_id or not principal.has_vm_perm("vm.console", vm.id):
            raise ConsoleDenied("Access to this VM console is denied", FORBIDDEN)
        if not vm.hv_id:
            raise ConsoleDenied("VM is not provisioned", UPSTREAM_NOT_FOUND)
        cluster = vm.cluster
        driver = get_driver()
        host = vm.host
        try:
            located = locate_vm(db, driver, vm) if (cluster.kind == "cluster" or host is None) else host
            if located is not None:
                host = located
                vm.host_id = host.id
                db.commit()
        except HyperVError as exc:
            log.warning("Owner lookup for %s failed: %s", vm.hv_name, exc)
        if host is None:
            raise ConsoleDenied("Cannot determine the host running this VM", UPSTREAM_NOT_FOUND)
        username = cluster.console_username or cluster.username
        password = decrypt_secret(cluster.console_password_enc if cluster.console_username else cluster.password_enc)
        user, domain = split_user(username)
        protocol, params = driver.console_connection(host.address, cluster.console_port, vm.hv_id, user, password,
                                                     domain)
        console_cfg = get_setting(db, "console")
        if protocol == "rdp" and console_cfg.get("keyboard_layout"):
            params["server-layout"] = console_cfg["keyboard_layout"]
        guacd = get_setting(db, "guacd")
        log_event(db, "vm.console.connect", user=principal.user, target_type="vm", target_id=vm.id,
                  target_name=vm.name, ip=ip, details={"host": host.name})
        db.commit()
        return {"protocol": protocol, "params": params, "guacd_host": guacd["host"], "guacd_port": int(guacd["port"]),
                "vm": vm.name, "host": host.name}
    finally:
        db.close()


def _int(value: str | None, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value or default)))
    except ValueError:
        return default


@router.websocket("/ws/console")
async def console_ws(websocket: WebSocket) -> None:
    qp = websocket.query_params
    width = _int(qp.get("width"), 1024, 320, 7680)
    height = _int(qp.get("height"), 768, 240, 4320)
    dpi = _int(qp.get("dpi"), 96, 48, 400)
    tz = qp.get("timezone") or None
    ip = (websocket.headers.get("x-forwarded-for") or (websocket.client.host if websocket.client else "")).split(",")[0]
    await websocket.accept(subprotocol="guacamole")

    async def fail(message: str, status: str) -> None:
        try:
            await websocket.send_text(encode("error", message, status))
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        target = await run_in_threadpool(prepare_console, websocket.cookies.get(SESSION_COOKIE), qp.get("ticket"), ip)
    except ConsoleDenied as exc:
        await fail(str(exc), exc.status)
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("Console preparation failed")
        await fail(f"Console error: {exc}", SERVER_ERROR)
        return

    try:
        guacd = await GuacdConnection.connect(target["guacd_host"], target["guacd_port"])
        conn_id = await guacd.handshake(target["protocol"], target["params"], width, height, dpi, tz)
    except (GuacError, asyncio.TimeoutError) as exc:
        log.warning("guacd handshake for %s failed: %s", target["vm"], exc)
        await fail(f"Cannot open console: {exc}", UPSTREAM_ERROR)
        return

    log.info("Console %s opened for VM %s on %s", conn_id, target["vm"], target["host"])
    send_lock = asyncio.Lock()

    async def ws_send(text: str) -> None:
        async with send_lock:
            await websocket.send_text(text)

    await ws_send(encode(INTERNAL_OPCODE, conn_id))
    leftover = guacd.drain_pending_raw()
    if leftover:
        await ws_send(leftover)

    async def browser_to_guacd() -> None:
        parser = InstructionParser()
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is None:
                data = msg.get("bytes") or b""
                text = data.decode("utf-8", "replace")
            for op, args, raw in parser.feed(text):
                if op == INTERNAL_OPCODE:
                    if args and args[0] == "ping":
                        await ws_send(raw)
                    continue
                await guacd.send(raw)

    async def guacd_to_browser() -> None:
        while True:
            text = await guacd.read_text()
            if text == "":
                return
            out = guacd.parser.complete_prefix(text)
            if out:
                await ws_send(out)

    tasks = [asyncio.create_task(browser_to_guacd()), asyncio.create_task(guacd_to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, ConnectionError)):
                log.info("Console tunnel ended: %s", exc)
    finally:
        await guacd.close()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("Console %s closed", conn_id)
