"""Hyper-V driver interface.

Every call targets exactly one Hyper-V host (an "endpoint").  Cluster-wide
information is obtained by running cluster cmdlets on any node; VM operations
always run on the node that currently owns the VM, so no credential
delegation (second hop) is needed for local CSV paths.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class HyperVError(Exception):
    """Error raised by a Hyper-V operation.

    ``code`` is a stable machine readable code (VM_NOT_FOUND, AUTH_FAILED,
    CONNECT_FAILED, ...) that the service layer may react on.
    """

    def __init__(self, message: str, code: str = "HV_ERROR", host: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.host = host

    def __str__(self) -> str:
        prefix = f"[{self.host}] " if self.host else ""
        return f"{prefix}{self.message}"


@dataclass
class Endpoint:
    address: str
    username: str
    password: str = field(repr=False)
    transport: str = "http"
    port: int = 5985
    auth: str = "ntlm"
    cert_validation: bool = False
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.address


class HyperVDriver(ABC):
    """Abstract driver.  All methods are blocking (run them in worker threads)."""

    name = "abstract"

    @abstractmethod
    def ping(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def discover(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def host_info(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def host_memory(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def cluster_info(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def node_states(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def list_vms(self, ep: Endpoint) -> dict[str, Any]: ...

    @abstractmethod
    def create_vm(self, ep: Endpoint, spec: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def make_ha(self, ep: Endpoint, vm_id: str, group: str) -> dict[str, Any]: ...

    @abstractmethod
    def vm_power(self, ep: Endpoint, vm_id: str, action: str) -> dict[str, Any]: ...

    @abstractmethod
    def vm_info(self, ep: Endpoint, vm_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def delete_vm(self, ep: Endpoint, args: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def snapshot(self, ep: Endpoint, vm_id: str, action: str, name: str | None = None,
                 snapshot_id: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def set_media(self, ep: Endpoint, vm_id: str, iso_path: str | None) -> dict[str, Any]: ...

    @abstractmethod
    def reconfigure(self, ep: Endpoint, vm_id: str, changes: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def find_owner(self, ep: Endpoint, group: str | None, vm_id: str | None) -> dict[str, Any]: ...

    @abstractmethod
    def test_path(self, ep: Endpoint, path: str) -> dict[str, Any]: ...

    def console_connection(self, host_address: str, port: int, vm_hv_id: str, username: str,
                           password: str, domain: str) -> tuple[str, dict[str, str]]:
        """Return (guacd protocol, parameters) for the VM console.

        Hyper-V console = RDP to <owner node>:2179 in "vmconnect" security mode
        with the VM id sent as the pre-connection BLOB.
        """
        params = {
            "hostname": host_address,
            "port": str(port),
            "security": "vmconnect",
            "ignore-cert": "true",
            "preconnection-blob": vm_hv_id,
            "username": username,
            "password": password,
            "domain": domain,
            "disable-audio": "true",
            "resize-method": "",
        }
        return "rdp", params
