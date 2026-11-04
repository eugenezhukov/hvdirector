from __future__ import annotations

import re
import unicodedata
import uuid

VM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
NETWORK_NAME_RE = re.compile(r"^[\w][\w .-]{0,63}$", re.UNICODE)


class VlanParseError(ValueError):
    pass


def parse_vlan_ranges(spec: str | list[int] | None) -> list[int]:
    """Parse "100-110, 120,130-131" into a sorted list of VLAN ids (1..4094)."""
    if spec is None:
        return []
    if isinstance(spec, list):
        values = set()
        for v in spec:
            iv = int(v)
            if not 1 <= iv <= 4094:
                raise VlanParseError(f"VLAN {iv} out of range 1-4094")
            values.add(iv)
        return sorted(values)
    result: set[int] = set()
    for part in re.split(r"[,;\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part)
        if not m:
            raise VlanParseError(f"Invalid VLAN range '{part}'")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start > end:
            start, end = end, start
        if start < 1 or end > 4094:
            raise VlanParseError(f"VLAN range '{part}' out of 1-4094")
        if end - start > 4094:
            raise VlanParseError("VLAN range too large")
        result.update(range(start, end + 1))
    return sorted(result)


def format_vlan_ranges(vlans: list[int]) -> str:
    """[100,101,102,120] -> "100-102,120"."""
    vlans = sorted(set(vlans))
    parts: list[str] = []
    i = 0
    while i < len(vlans):
        j = i
        while j + 1 < len(vlans) and vlans[j + 1] == vlans[j] + 1:
            j += 1
        parts.append(str(vlans[i]) if i == j else f"{vlans[i]}-{vlans[j]}")
        i = j + 1
    return ",".join(parts)


_ISO_BAD_CHARS = re.compile(r"[^A-Za-z0-9._()\[\]+-]+")
_CYR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
     "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"], strict=True))


def _translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _CYR:
            t = _CYR[low]
            out.append(t.capitalize() if ch != low and t else t)
        else:
            out.append(ch)
    return "".join(out)


def sanitize_iso_name(name: str) -> str:
    """Make an original ISO file name safe for an SMB path (keeps it readable)."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    stem = name[:-4] if name.lower().endswith(".iso") else name
    stem = unicodedata.normalize("NFKD", _translit(stem)).encode("ascii", "ignore").decode("ascii")
    stem = _ISO_BAD_CHARS.sub("_", stem).strip("._ ")[:180] or "image"
    return stem + ".iso"


def unique_iso_file_name(original: str) -> str:
    """Prefix a GUID so that names uploaded by different tenants never collide."""
    return f"{uuid.uuid4()}_{sanitize_iso_name(original)}"


def hv_vm_name(tenant_slug: str, vm_name: str) -> str:
    """Hyper-V VM / cluster role name.  Slugs cannot contain "_", so the first "_"
    unambiguously separates the organization from the VM name (unique cluster-wide)."""
    return f"{tenant_slug}_{vm_name}"
