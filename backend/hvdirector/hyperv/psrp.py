"""Real Hyper-V driver: PowerShell Remoting Protocol over WinRM (pypsrp).

Scripts live in ./scripts/*.ps1.  Every script receives a single JSON string
parameter ($ArgsJson -> $A), so user-supplied values are never interpolated
into PowerShell source.  A script returns one hashtable that is serialised to
JSON with a marker prefix.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from pypsrp.exceptions import AuthenticationError, WinRMTransportError, WSManFaultError
from pypsrp.powershell import PowerShell, RunspacePool
from pypsrp.wsman import WSMan

from .base import Endpoint, HyperVDriver, HyperVError

log = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent / "scripts"
MARKER = "HVDJSON:"

PRELUDE = r"""param([string]$ArgsJson)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$A = $null
if ($ArgsJson) { $A = ConvertFrom-Json -InputObject $ArgsJson }
"""

WRAPPER = r"""
try {
    $__r = & {
%BODY%
    }
    $__o = @{ ok = $true; data = $__r }
} catch {
    $__o = @{ ok = $false; error = [string]$_.Exception.Message;
              line = [int]$_.InvocationInfo.ScriptLineNumber; command = [string]$_.InvocationInfo.MyCommand }
}
'HVDJSON:' + (ConvertTo-Json -InputObject $__o -Depth 10 -Compress)
"""

_ERR_RE = re.compile(r"HVD:([A-Z_]+):(.*)", re.S)


@lru_cache(maxsize=64)
def build_script(name: str) -> str:
    common = (SCRIPTS_DIR / "_common.ps1").read_text(encoding="utf-8")
    body = (SCRIPTS_DIR / f"{name}.ps1").read_text(encoding="utf-8")
    return PRELUDE + common + WRAPPER.replace("%BODY%", textwrap.indent(body, "        "))


def parse_output(output: list[Any], errors: list[str], host: str) -> Any:
    payload = None
    for item in reversed(output):
        text = item if isinstance(item, str) else str(item)
        if text.startswith(MARKER):
            payload = json.loads(text[len(MARKER):])
            break
    if payload is None:
        detail = "; ".join(errors) or "script produced no result"
        raise HyperVError(detail, "SCRIPT_ERROR", host)
    if not payload.get("ok"):
        message = payload.get("error") or "unknown error"
        m = _ERR_RE.search(message)
        if m:
            raise HyperVError(m.group(2).strip(), m.group(1), host)
        where = f" [line {payload['line']}: {payload.get('command') or '?'}]" if payload.get("line") else ""
        raise HyperVError(message + where, "PS_ERROR", host)
    data = payload.get("data")
    if isinstance(data, list):  # stray pipeline output before "return"
        dicts = [d for d in data if isinstance(d, dict)]
        data = dicts[-1] if dicts else None
    return data


class PsrpDriver(HyperVDriver):
    name = "psrp"

    def __init__(self, operation_timeout: int = 20, read_timeout: int = 30, connection_timeout: int = 15) -> None:
        self.operation_timeout = operation_timeout
        self.read_timeout = read_timeout
        self.connection_timeout = connection_timeout

    # ------------------------------------------------------------------ core
    def run(self, ep: Endpoint, script: str, args: dict[str, Any] | None = None) -> Any:
        source = build_script(script)
        auth = ep.auth or "negotiate"
        ssl = ep.transport == "https"
        if auth == "basic" and not ssl:
            raise HyperVError("Basic authentication requires HTTPS", "CONFIG_ERROR", ep.display)
        wsman = WSMan(
            ep.address,
            port=ep.port,
            username=ep.username,
            password=ep.password,
            ssl=ssl,
            auth=auth,
            cert_validation=ep.cert_validation,
            connection_timeout=self.connection_timeout,
            operation_timeout=self.operation_timeout,
            read_timeout=self.read_timeout,
            encryption="auto",
            locale="en-US",
        )
        log.debug("Running %s on %s", script, ep.display)
        try:
            with wsman, RunspacePool(wsman) as pool:
                ps = PowerShell(pool)
                ps.add_script(source)
                ps.add_parameter("ArgsJson", json.dumps(args or {}, default=str))
                output = ps.invoke()
                errors = [str(e) for e in ps.streams.error]
        except HyperVError:
            raise
        except AuthenticationError as exc:
            raise HyperVError(f"Authentication failed: {exc}", "AUTH_FAILED", ep.display) from exc
        except WinRMTransportError as exc:
            code = "AUTH_FAILED" if getattr(exc, "code", None) == 401 else "CONNECT_FAILED"
            raise HyperVError(f"WinRM transport error: {exc}", code, ep.display) from exc
        except WSManFaultError as exc:
            raise HyperVError(f"WinRM fault: {exc}", "WINRM_FAULT", ep.display) from exc
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise HyperVError(f"Cannot connect to {ep.address}:{ep.port}: {exc}", "CONNECT_FAILED",
                              ep.display) from exc
        except Exception as exc:  # noqa: BLE001 - surface anything else as driver error
            raise HyperVError(f"{type(exc).__name__}: {exc}", "HV_ERROR", ep.display) from exc
        return parse_output(output, errors, ep.display)

    # --------------------------------------------------------------- inventory
    def ping(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "ping")

    def discover(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "discover")

    def host_info(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "host_info")

    def host_memory(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "host_memory")

    def cluster_info(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "cluster_info")

    def node_states(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "node_states")

    def list_vms(self, ep: Endpoint) -> dict[str, Any]:
        return self.run(ep, "list_vms")

    def test_path(self, ep: Endpoint, path: str) -> dict[str, Any]:
        return self.run(ep, "test_path", {"path": path})

    def find_owner(self, ep: Endpoint, group: str | None, vm_id: str | None) -> dict[str, Any]:
        return self.run(ep, "find_owner", {"group": group, "vm_id": vm_id})

    # --------------------------------------------------------------------- VMs
    def create_vm(self, ep: Endpoint, spec: dict[str, Any]) -> dict[str, Any]:
        return self.run(ep, "vm_create", spec)

    def make_ha(self, ep: Endpoint, vm_id: str, group: str) -> dict[str, Any]:
        return self.run(ep, "vm_make_ha", {"id": vm_id, "group": group})

    def vm_power(self, ep: Endpoint, vm_id: str, action: str) -> dict[str, Any]:
        return self.run(ep, "vm_power", {"id": vm_id, "action": action})

    def vm_info(self, ep: Endpoint, vm_id: str) -> dict[str, Any]:
        return self.run(ep, "vm_info", {"id": vm_id})

    def delete_vm(self, ep: Endpoint, args: dict[str, Any]) -> dict[str, Any]:
        return self.run(ep, "vm_delete", args)

    def snapshot(self, ep: Endpoint, vm_id: str, action: str, name: str | None = None,
                 snapshot_id: str | None = None) -> dict[str, Any]:
        return self.run(ep, "vm_snapshot", {"id": vm_id, "action": action, "name": name,
                                             "snapshot_id": snapshot_id})

    def set_media(self, ep: Endpoint, vm_id: str, iso_path: str | None) -> dict[str, Any]:
        return self.run(ep, "vm_media", {"id": vm_id, "iso": iso_path})

    def reconfigure(self, ep: Endpoint, vm_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return self.run(ep, "vm_reconfigure", {"id": vm_id, **changes})

    def vm_metrics(self, ep: Endpoint, vm_ids: list[str]) -> dict[str, Any]:
        return self.run(ep, "vm_metrics", {"ids": vm_ids})
