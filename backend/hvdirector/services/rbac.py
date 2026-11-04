"""Simplified tenant role model.

Roles: tenant_admin, audit, console.  Each assignment is either tenant-wide
(vm_id NULL) or delegated on one VM.  A tenant-wide assignment grants the
role's VM permissions on every VM of the tenant plus the tenant-level
permissions; a VM-scoped assignment only grants VM permissions on that VM.
Provider administrators (users without tenant) manage the platform and see
tenants read-only through the provider portal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..models import Tenant, User, UserSession

ROLES = ("tenant_admin", "audit", "console")

VM_PERMS: dict[str, set[str]] = {
    "tenant_admin": {"vm.view", "vm.power", "vm.snapshot", "vm.media", "vm.console", "vm.edit", "vm.delete"},
    "audit": {"vm.view"},
    "console": {"vm.view", "vm.console"},
}

TENANT_PERMS: dict[str, set[str]] = {
    "tenant_admin": {"tenant.view", "vm.create", "network.view", "network.manage", "iso.view", "iso.manage",
                     "user.view", "user.manage", "task.view", "event.view", "router.view", "router.manage"},
    "audit": {"tenant.view", "network.view", "iso.view", "user.view", "task.view", "event.view", "router.view"},
    "console": set(),
}

# Tenant-level read permissions implied by a VM-scoped role (needed to operate the VM).
VM_SCOPED_IMPLIED: dict[str, set[str]] = {
    "tenant_admin": {"network.view", "iso.view"},
    "audit": set(),
    "console": set(),
}


@dataclass
class Principal:
    user: User
    session: UserSession | None
    tenant: Tenant | None
    assignments: list[tuple[str, uuid.UUID | None]] = field(default_factory=list)
    ip: str = ""

    @property
    def is_provider(self) -> bool:
        return self.user.tenant_id is None

    @property
    def tenant_id(self) -> uuid.UUID | None:
        return self.user.tenant_id

    def tenant_perms(self) -> set[str]:
        perms: set[str] = set()
        for role, vm_id in self.assignments:
            if vm_id is None:
                perms |= TENANT_PERMS.get(role, set())
            else:
                perms |= VM_SCOPED_IMPLIED.get(role, set())
        return perms

    def has_tenant_perm(self, perm: str) -> bool:
        return perm in self.tenant_perms()

    def vm_perms(self, vm_id: uuid.UUID) -> set[str]:
        perms: set[str] = set()
        for role, scope in self.assignments:
            if scope is None or scope == vm_id:
                perms |= VM_PERMS.get(role, set())
        return perms

    def has_vm_perm(self, perm: str, vm_id: uuid.UUID) -> bool:
        return perm in self.vm_perms(vm_id)

    def sees_all_vms(self) -> bool:
        return any(scope is None and "vm.view" in VM_PERMS.get(role, set()) for role, scope in self.assignments)

    def scoped_vm_ids(self) -> set[uuid.UUID]:
        return {scope for _, scope in self.assignments if scope is not None}

    def all_permissions(self) -> list[str]:
        perms = set(self.tenant_perms())
        if self.sees_all_vms():
            for role, scope in self.assignments:
                if scope is None:
                    perms |= VM_PERMS.get(role, set())
        elif self.scoped_vm_ids():
            perms.add("vm.view")
        return sorted(perms)
