"""Database schema (PostgreSQL)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------
# Global settings (ISO share, guacd endpoint, console options, ...)
# ---------------------------------------------------------------------------


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Infrastructure: clusters / standalone hosts, nodes, storage
# ---------------------------------------------------------------------------


class Cluster(Base):
    """A Hyper-V failover cluster or a standalone Hyper-V host (compute resource)."""

    __tablename__ = "clusters"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # cluster | standalone
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cluster_name: Mapped[str | None] = mapped_column(String(255))  # cluster name object (CNO)
    cluster_fqdn: Mapped[str | None] = mapped_column(String(255))
    cluster_address: Mapped[str | None] = mapped_column(String(64))
    domain: Mapped[str | None] = mapped_column(String(255))
    seed_address: Mapped[str] = mapped_column(String(255), nullable=False)  # address used in the wizard

    # WinRM / PowerShell remoting
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    winrm_transport: Mapped[str] = mapped_column(String(8), default="http", nullable=False)  # http|https
    winrm_port: Mapped[int] = mapped_column(Integer, default=5985, nullable=False)
    winrm_auth: Mapped[str] = mapped_column(String(16), default="ntlm", nullable=False)
    winrm_cert_validation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Console (guacd -> host:2179 vmconnect).  Empty = use management credentials.
    console_username: Mapped[str | None] = mapped_column(String(255))
    console_password_enc: Mapped[str | None] = mapped_column(Text)
    console_port: Mapped[int] = mapped_column(Integer, default=2179, nullable=False)

    vswitch_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    ha_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vm_folder: Mapped[str] = mapped_column(String(64), default="HVDirector", nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    status_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_refresh_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()

    hosts: Mapped[list[Host]] = relationship(back_populates="cluster", cascade="all, delete-orphan",
                                             order_by="Host.name")
    datastores: Mapped[list[Datastore]] = relationship(back_populates="cluster", cascade="all, delete-orphan",
                                                       order_by="Datastore.name")

    __table_args__ = (CheckConstraint("kind in ('cluster','standalone')", name="ck_cluster_kind"),)


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[uuid.UUID] = _pk()
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    fqdn: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="Unknown", nullable=False)
    in_cluster: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reachable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    os_version: Mapped[str | None] = mapped_column(String(255))
    logical_cpus: Mapped[int | None] = mapped_column(Integer)
    memory_total_mb: Mapped[int | None] = mapped_column(Integer)
    memory_free_mb: Mapped[int | None] = mapped_column(Integer)
    vm_count: Mapped[int | None] = mapped_column(Integer)
    switches: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()

    cluster: Mapped[Cluster] = relationship(back_populates="hosts")

    __table_args__ = (Index("ux_hosts_cluster_name", "cluster_id", func.lower(text("name")), unique=True),)


class StorageClass(Base):
    """Provider-defined storage class (tier), e.g. "Gold SSD", "Bronze HDD"."""

    __tablename__ = "storage_classes"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1 = fastest
    created_at: Mapped[dt.datetime] = _created()


class Datastore(Base):
    """A location for VM files on a cluster: CSV path, SMB3 share or local volume."""

    __tablename__ = "datastores"

    id: Mapped[uuid.UUID] = _pk()
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    storage_class_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("storage_classes.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="csv", nullable=False)  # csv | smb | local
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    discovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    capacity_gb: Mapped[float | None] = mapped_column(Float)
    free_gb: Mapped[float | None] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    cluster: Mapped[Cluster] = relationship(back_populates="datastores")
    storage_class: Mapped[StorageClass | None] = relationship()

    __table_args__ = (Index("ux_datastores_cluster_path", "cluster_id", func.lower(text("path")), unique=True),)


# ---------------------------------------------------------------------------
# Tenants, quotas, VLAN pools
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id", ondelete="RESTRICT"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # NULL = unlimited
    quota_vcpu: Mapped[int | None] = mapped_column(Integer)
    quota_memory_mb: Mapped[int | None] = mapped_column(Integer)
    quota_vms: Mapped[int | None] = mapped_column(Integer)
    quota_iso_gb: Mapped[int | None] = mapped_column(Integer)
    quota_snapshots: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = _created()

    cluster: Mapped[Cluster] = relationship()
    storage_quotas: Mapped[list[TenantStorageQuota]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    vlans: Mapped[list[TenantVlan]] = relationship(back_populates="tenant", cascade="all, delete-orphan",
                                                   order_by="TenantVlan.vlan_id")


class TenantStorageQuota(Base):
    """Per-storage-class disk quota.  A tenant may only use classes listed here."""

    __tablename__ = "tenant_storage_quotas"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    storage_class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_classes.id", ondelete="CASCADE"), primary_key=True
    )
    limit_gb: Mapped[int | None] = mapped_column(Integer)  # NULL = unlimited

    tenant: Mapped[Tenant] = relationship(back_populates="storage_quotas")
    storage_class: Mapped[StorageClass] = relationship()


class TenantVlan(Base):
    """A VLAN of the pre-created L2 underlay assigned to exactly one tenant."""

    __tablename__ = "tenant_vlans"

    vlan_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="vlans")

    __table_args__ = (CheckConstraint("vlan_id between 1 and 4094", name="ck_vlan_range"),)


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    vlan_id: Mapped[int] = mapped_column(
        ForeignKey("tenant_vlans.vlan_id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cidr: Mapped[str | None] = mapped_column(String(64))
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = _created()

    dhcp: Mapped[NetworkDhcp | None] = relationship(back_populates="network", cascade="all, delete-orphan",
                                                    passive_deletes=True)
    dhcp_bindings: Mapped[list[DhcpBinding]] = relationship(back_populates="network", cascade="all, delete-orphan",
                                                            passive_deletes=True)

    __table_args__ = (Index("ux_networks_tenant_name", "tenant_id", func.lower(text("name")), unique=True),)


# ---------------------------------------------------------------------------
# ISO images
# ---------------------------------------------------------------------------


class Iso(Base):
    __tablename__ = "isos"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="uploading", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _created()


# ---------------------------------------------------------------------------
# Virtual machines
# ---------------------------------------------------------------------------


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id", ondelete="RESTRICT"), nullable=False)
    host_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hosts.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # "vm" = tenant workload, "router" = virtual router (edge) VM managed by HV Director
    kind: Mapped[str] = mapped_column(String(16), default="vm", server_default="vm", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hv_name: Mapped[str] = mapped_column(String(128), nullable=False)
    hv_id: Mapped[str | None] = mapped_column(String(36))
    cluster_group: Mapped[str | None] = mapped_column(String(255))
    generation: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    secure_boot: Mapped[str] = mapped_column(String(16), default="windows", nullable=False)  # off|windows|linux
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="creating", nullable=False)
    power_state: Mapped[str] = mapped_column(String(32), default="Unknown", nullable=False)
    iso_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("isos.id", ondelete="SET NULL"))
    guest_ips: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    cpu_usage: Mapped[int | None] = mapped_column(Integer)
    memory_assigned_mb: Mapped[int | None] = mapped_column(Integer)
    uptime_s: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # last raw (cumulative) load counters read from Hyper-V; rates are computed from consecutive readings
    metrics_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _created()

    tenant: Mapped[Tenant] = relationship()
    cluster: Mapped[Cluster] = relationship()
    host: Mapped[Host | None] = relationship()
    iso: Mapped[Iso | None] = relationship()
    disks: Mapped[list[VMDisk]] = relationship(back_populates="vm", cascade="all, delete-orphan",
                                               order_by="VMDisk.position")
    nics: Mapped[list[VMNic]] = relationship(back_populates="vm", cascade="all, delete-orphan",
                                             order_by="VMNic.position")
    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="vm", cascade="all, delete-orphan",
                                                     order_by="Snapshot.created_at")

    __table_args__ = (
        Index("ux_vms_tenant_name", "tenant_id", func.lower(text("name")), unique=True),
        Index("ux_vms_hv_id", "hv_id", unique=True, postgresql_where=text("hv_id is not null")),
    )


class VMDisk(Base):
    __tablename__ = "vm_disks"

    id: Mapped[uuid.UUID] = _pk()
    vm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), nullable=False)
    datastore_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datastores.id", ondelete="RESTRICT"),
                                                    nullable=False)
    storage_class_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_classes.id", ondelete="RESTRICT"),
                                                        nullable=False)
    size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    path: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[dt.datetime] = _created()

    vm: Mapped[VM] = relationship(back_populates="disks")
    datastore: Mapped[Datastore] = relationship()
    storage_class: Mapped[StorageClass] = relationship()


class VMNic(Base):
    __tablename__ = "vm_nics"

    id: Mapped[uuid.UUID] = _pk()
    vm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), nullable=False)
    network_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("networks.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hv_name: Mapped[str | None] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(32))

    vm: Mapped[VM] = relationship(back_populates="nics")
    network: Mapped[Network | None] = relationship()


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = _pk()
    vm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    hv_id: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="creating", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _created()

    vm: Mapped[VM] = relationship(back_populates="snapshots")


# ---------------------------------------------------------------------------
# Users, roles, sessions
# ---------------------------------------------------------------------------


class User(Base):
    """Local user.  tenant_id NULL = provider (system) administrator."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="ru", nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()

    tenant: Mapped[Tenant | None] = relationship()
    roles: Mapped[list[RoleAssignment]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ux_users_tenant_username", "tenant_id", func.lower(text("username")), unique=True,
              postgresql_where=text("tenant_id is not null")),
        Index("ux_users_provider_username", func.lower(text("username")), unique=True,
              postgresql_where=text("tenant_id is null")),
    )


class RoleAssignment(Base):
    """Role of a tenant user.  vm_id NULL = whole tenant, otherwise delegated on one VM."""

    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    vm_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = _created()

    user: Mapped[User] = relationship(back_populates="roles")
    vm: Mapped[VM | None] = relationship()

    __table_args__ = (
        CheckConstraint("role in ('tenant_admin','audit','console')", name="ck_role_name"),
        Index("ux_roles_tenant_scope", "user_id", "role", unique=True, postgresql_where=text("vm_id is null")),
        Index("ux_roles_vm_scope", "user_id", "role", "vm_id", unique=True,
              postgresql_where=text("vm_id is not null")),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()


class ConsoleTicket(Base):
    """Short-lived one-time ticket that authorises a console WebSocket."""

    __tablename__ = "console_tickets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    vm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# Tasks (background operations) and audit events
# ---------------------------------------------------------------------------


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    lock_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_tenant_created", "tenant_id", "created_at"),
        Index("ix_tasks_target", "target_id"),
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(),
                                            nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    __table_args__ = (Index("ix_events_tenant_ts", "tenant_id", "ts"), Index("ix_events_ts", "ts"))


# ---------------------------------------------------------------------------
# Virtual routers (simplified Cloud Director Edge)
# ---------------------------------------------------------------------------


class ExternalNetwork(Base):
    """Provider network outside the tenants: WAN uplink segment or OOB management segment."""

    __tablename__ = "external_networks"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)  # wan | mgmt
    vlan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    vswitch_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)  # "" = cluster switch
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    __table_args__ = (
        CheckConstraint("purpose in ('wan','mgmt')", name="ck_extnet_purpose"),
        CheckConstraint("vlan_id between 1 and 4094", name="ck_extnet_vlan"),
    )


class ExternalIp(Base):
    """An address of an external network allocated to a tenant (WAN IP or router management IP)."""

    __tablename__ = "external_ips"

    id: Mapped[uuid.UUID] = _pk()
    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_networks.id", ondelete="RESTRICT"),
                                                  nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    network: Mapped[ExternalNetwork] = relationship()

    __table_args__ = (Index("ux_external_ips_addr", "network_id", "address", unique=True),)


class Router(Base):
    """Tenant virtual router: Debian 13 VM, tenant side in a VRF, management in the global table."""

    __tablename__ = "routers"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), unique=True,
                                                 nullable=False)
    vm_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vms.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="deploying", nullable=False)
    status_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mgmt_ip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_ips.id", ondelete="RESTRICT"), nullable=False)
    storage_class_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_classes.id", ondelete="RESTRICT"),
                                                        nullable=False)
    mgmt_mac: Mapped[str] = mapped_column(String(17), nullable=False)
    wan_mac: Mapped[str] = mapped_column(String(17), nullable=False)
    lan_mac: Mapped[str] = mapped_column(String(17), nullable=False)
    firewall_stateful: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    firewall_default_action: Mapped[str] = mapped_column(String(8), default="drop", nullable=False)
    firewall_log_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    applied_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    root_password_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)
    host_key_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)
    host_key_pub: Mapped[str] = mapped_column(Text, default="", nullable=False)
    install_iso_file: Mapped[str | None] = mapped_column(String(320))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _created()

    tenant: Mapped[Tenant] = relationship()
    vm: Mapped[VM | None] = relationship()
    mgmt_ip: Mapped[ExternalIp] = relationship()
    storage_class: Mapped[StorageClass] = relationship()
    networks: Mapped[list[RouterNetwork]] = relationship(back_populates="router", cascade="all, delete-orphan")
    firewall_rules: Mapped[list[FirewallRule]] = relationship(back_populates="router", cascade="all, delete-orphan",
                                                              order_by="FirewallRule.position")
    nat_rules: Mapped[list[NatRule]] = relationship(back_populates="router", cascade="all, delete-orphan",
                                                    order_by="NatRule.position")

    __table_args__ = (
        CheckConstraint("firewall_default_action in ('drop','accept')", name="ck_router_default_action"),
    )


class RouterNetwork(Base):
    """Tenant network connected to the router: a VLAN SVI on the router OS (gateway address)."""

    __tablename__ = "router_networks"

    router_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), primary_key=True)
    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("networks.id", ondelete="RESTRICT"), primary_key=True)

    router: Mapped[Router] = relationship(back_populates="networks")
    network: Mapped[Network] = relationship()


class IpSet(Base):
    """Named list of IPv4 addresses / CIDRs / ranges used in firewall rules."""

    __tablename__ = "ip_sets"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    entries: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    __table_args__ = (Index("ux_ip_sets_tenant_name", "tenant_id", func.lower(text("name")), unique=True),)


class FirewallRule(Base):
    __tablename__ = "firewall_rules"

    id: Mapped[uuid.UUID] = _pk()
    router_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    action: Mapped[str] = mapped_column(String(8), default="allow", nullable=False)  # allow | drop | reject
    protocol: Mapped[str] = mapped_column(String(8), default="any", nullable=False)  # any|tcp|udp|tcp_udp|icmp
    # [{"type": "cidr"|"ipset"|"network", "value": "..."}]; empty list = any
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    destinations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    src_ports: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    dst_ports: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    log: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    router: Mapped[Router] = relationship(back_populates="firewall_rules")

    __table_args__ = (
        CheckConstraint("action in ('allow','drop','reject')", name="ck_fw_action"),
        CheckConstraint("protocol in ('any','tcp','udp','tcp_udp','icmp')", name="ck_fw_protocol"),
    )


class NatRule(Base):
    __tablename__ = "nat_rules"

    id: Mapped[uuid.UUID] = _pk()
    router_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # snat | dnat
    external_ip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_ips.id", ondelete="RESTRICT"),
                                                      nullable=False)
    # SNAT: internal source (IP/CIDR); DNAT: internal destination host (IP)
    internal_address: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol: Mapped[str] = mapped_column(String(8), default="any", nullable=False)  # any|tcp|udp|tcp_udp
    external_port: Mapped[str] = mapped_column(String(32), default="", nullable=False)  # DNAT: port or range
    internal_port: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # DNAT: translated port
    # Optional peer filter: SNAT -> destination, DNAT -> source (IP/CIDR)
    match_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    log: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    router: Mapped[Router] = relationship(back_populates="nat_rules")
    external_ip: Mapped[ExternalIp] = relationship()

    __table_args__ = (
        CheckConstraint("kind in ('snat','dnat')", name="ck_nat_kind"),
        CheckConstraint("protocol in ('any','tcp','udp','tcp_udp')", name="ck_nat_protocol"),
    )


class NetworkDhcp(Base):
    """DHCP server of a tenant network, served by the router on the network SVI (systemd-networkd)."""

    __tablename__ = "network_dhcp"

    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("networks.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    range_start: Mapped[str] = mapped_column(String(64), nullable=False)
    range_end: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_time: Mapped[int] = mapped_column(Integer, default=86400, nullable=False)  # seconds
    dns_servers: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    ntp_servers: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    domain_name: Mapped[str] = mapped_column(String(253), default="", nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )

    network: Mapped[Network] = relationship(back_populates="dhcp")

    __table_args__ = (CheckConstraint("lease_time between 300 and 31536000", name="ck_dhcp_lease_time"),)


class DhcpBinding(Base):
    """Static DHCP binding (reservation): fixed IPv4 address for a MAC address."""

    __tablename__ = "dhcp_bindings"

    id: Mapped[uuid.UUID] = _pk()
    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("networks.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    mac: Mapped[str] = mapped_column(String(17), nullable=False)  # aa:bb:cc:dd:ee:ff
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = _created()

    network: Mapped[Network] = relationship(back_populates="dhcp_bindings")

    __table_args__ = (
        Index("ux_dhcp_bindings_mac", "network_id", "mac", unique=True),
        Index("ux_dhcp_bindings_address", "network_id", "address", unique=True),
    )


class VmMetric(Base):
    """VM load sample: averages over one collection interval (CPU %, memory, disk and network rates)."""

    __tablename__ = "vm_metrics"

    vm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vms.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    mem_assigned_mb: Mapped[int | None] = mapped_column(Integer)
    mem_demand_mb: Mapped[int | None] = mapped_column(Integer)
    disk_read_bps: Mapped[float | None] = mapped_column(Float)
    disk_write_bps: Mapped[float | None] = mapped_column(Float)
    disk_iops: Mapped[float | None] = mapped_column(Float)
    net_rx_bps: Mapped[float | None] = mapped_column(Float)
    net_tx_bps: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_vm_metrics_ts", "ts"),)
