"""ISO library on the share that all Hyper-V hosts can read (\\\\shared\\iso).

The management server writes uploaded images directly to the share with an
SMB client (smbprotocol), so nothing has to be mounted on the Linux box.
Hosts attach images by their UNC path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from ..errors import AppError
from ..security import decrypt_secret

_UNC_RE = re.compile(r"^\\\\([^\\]+)\\([^\\]+)(\\.*)?$")


@dataclass
class IsoStore:
    backend: str
    unc: str
    server: str
    username: str
    password: str
    local_path: str

    @classmethod
    def from_setting(cls, value: dict[str, Any]) -> IsoStore:
        return cls(
            backend=value.get("backend", "smb"),
            unc=(value.get("unc") or "").rstrip("\\"),
            server=value.get("server") or "",
            username=value.get("username") or "",
            password=decrypt_secret(value.get("password_enc")),
            local_path=value.get("local_path") or "/var/lib/hvdirector/iso",
        )

    # ------------------------------------------------------------- paths
    def host_path(self, file_name: str) -> str:
        """UNC path used by Hyper-V hosts (Set-VMDvdDrive -Path)."""
        return f"{self.unc}\\{file_name}"

    def _smb_path(self, file_name: str | None = None) -> str:
        m = _UNC_RE.match(self.unc)
        if not m:
            raise AppError("iso_share_invalid", f"Invalid UNC path: {self.unc}")
        server = self.server or m.group(1)
        rest = m.group(3) or ""
        base = f"\\\\{server}\\{m.group(2)}{rest}"
        return f"{base}\\{file_name}" if file_name else base

    def _local(self, file_name: str) -> str:
        return os.path.join(self.local_path, file_name)

    def _smb_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"connection_timeout": 30}
        if self.username:
            kw["username"] = self.username
            kw["password"] = self.password
        return kw

    # -------------------------------------------------------------- ops
    def create(self, file_name: str) -> None:
        if self.backend == "local":
            os.makedirs(self.local_path, exist_ok=True)
            with open(self._local(file_name), "wb"):
                pass
            return
        import smbclient

        try:
            with smbclient.open_file(self._smb_path(file_name), mode="wb", **self._smb_kwargs()):
                pass
        except Exception as exc:  # noqa: BLE001
            raise AppError("iso_share_error", f"Cannot create file on ISO share: {exc}") from exc

    def write_chunk(self, file_name: str, offset: int, data: bytes) -> None:
        if self.backend == "local":
            with open(self._local(file_name), "r+b") as fh:
                fh.seek(offset)
                fh.write(data)
            return
        import smbclient

        try:
            with smbclient.open_file(self._smb_path(file_name), mode="r+b", **self._smb_kwargs()) as fh:
                fh.seek(offset)
                fh.write(data)
        except Exception as exc:  # noqa: BLE001
            raise AppError("iso_share_error", f"Cannot write to ISO share: {exc}") from exc

    def delete(self, file_name: str) -> None:
        if self.backend == "local":
            try:
                os.remove(self._local(file_name))
            except FileNotFoundError:
                pass
            return
        import smbclient

        try:
            smbclient.remove(self._smb_path(file_name), **self._smb_kwargs())
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            if "STATUS_OBJECT_NAME_NOT_FOUND" in str(exc):
                return
            raise AppError("iso_share_error", f"Cannot delete file on ISO share: {exc}") from exc

    def test(self) -> dict[str, Any]:
        """Write and remove a probe file; returns free space when available."""
        probe = ".hvdirector-probe"
        self.create(probe)
        self.write_chunk(probe, 0, b"ok")
        self.delete(probe)
        result: dict[str, Any] = {"ok": True, "path": self.unc if self.backend == "smb" else self.local_path}
        if self.backend == "local":
            st = os.statvfs(self.local_path)
            result["free_gb"] = round(st.f_bavail * st.f_frsize / 1024**3, 1)
        return result
