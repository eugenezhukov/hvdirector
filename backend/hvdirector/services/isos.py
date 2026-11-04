"""ISO upload workflow: init -> sequential chunks -> ready.  Shared by provider and tenant APIs."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError, Conflict
from ..models import VM, Iso, Tenant, User
from ..utils import unique_iso_file_name
from .quotas import Delta, check_quota, lock_tenant
from .isostore import IsoStore
from .settings import get_setting

MAX_ISO_BYTES = 64 * 1024**3
MAX_CHUNK_BYTES = 64 * 1024**2


def store(db: Session) -> IsoStore:
    return IsoStore.from_setting(get_setting(db, "iso_share"))


def init_upload(db: Session, tenant: Tenant | None, user: User, name: str, size_bytes: int,
                description: str = "") -> Iso:
    name = (name or "").strip()
    if not name.lower().endswith(".iso"):
        raise AppError("iso_bad_name", "Only .iso files can be uploaded")
    if size_bytes <= 0 or size_bytes > MAX_ISO_BYTES:
        raise AppError("iso_bad_size", "Invalid ISO size", max_gb=MAX_ISO_BYTES // 1024**3)
    if tenant is not None:
        tenant = lock_tenant(db, tenant.id)
        check_quota(db, tenant, Delta(iso_bytes=size_bytes))
    file_name = unique_iso_file_name(name)  # "<guid>_<original>.iso" - unique across tenants
    iso = Iso(tenant_id=tenant.id if tenant else None, name=name[:255], file_name=file_name,
              description=description, size_bytes=size_bytes, uploaded_bytes=0, status="uploading",
              created_by=user.id)
    store(db).create(file_name)
    db.add(iso)
    db.flush()
    return iso


def write_chunk(db: Session, iso_id: uuid.UUID, offset: int, data: bytes) -> Iso:
    iso = db.execute(select(Iso).where(Iso.id == iso_id).with_for_update()).scalar_one()
    if iso.status != "uploading":
        raise Conflict("iso_not_uploading", "Upload is already finished")
    if offset != iso.uploaded_bytes:
        raise Conflict("iso_bad_offset", "Unexpected chunk offset", expected=iso.uploaded_bytes)
    if len(data) > MAX_CHUNK_BYTES:
        raise AppError("iso_chunk_too_large", "Chunk too large")
    if offset + len(data) > iso.size_bytes:
        raise AppError("iso_too_much_data", "More data than declared size")
    store(db).write_chunk(iso.file_name, offset, data)
    iso.uploaded_bytes = offset + len(data)
    if iso.uploaded_bytes == iso.size_bytes:
        iso.status = "ready"
    return iso


def delete_iso(db: Session, iso: Iso) -> None:
    in_use = db.scalars(select(VM.name).where(VM.iso_id == iso.id)).all()
    if in_use:
        raise Conflict("iso_in_use", "ISO is attached to VMs", vms=", ".join(in_use))
    store(db).delete(iso.file_name)
    db.delete(iso)
