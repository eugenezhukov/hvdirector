import uuid

import pytest

from hvdirector.guac import InstructionParser, encode
from hvdirector.hyperv.psrp import build_script, parse_output
from hvdirector.hyperv.base import HyperVError
from hvdirector.services.rbac import Principal
from hvdirector.utils import (VlanParseError, format_vlan_ranges, parse_vlan_ranges, sanitize_iso_name,
                              unique_iso_file_name)


def test_vlan_ranges():
    assert parse_vlan_ranges("100-103, 110;120 121") == [100, 101, 102, 103, 110, 120, 121]
    assert parse_vlan_ranges("5-3") == [3, 4, 5]
    assert format_vlan_ranges([100, 101, 102, 110, 120, 121]) == "100-102,110,120-121"
    assert parse_vlan_ranges("") == []
    with pytest.raises(VlanParseError):
        parse_vlan_ranges("0-5")
    with pytest.raises(VlanParseError):
        parse_vlan_ranges("4095")
    with pytest.raises(VlanParseError):
        parse_vlan_ranges("abc")


def test_iso_names():
    assert sanitize_iso_name("C:\\Users\\me\\Windows Server 2022.iso") == "Windows_Server_2022.iso"
    assert sanitize_iso_name("ubuntu") == "ubuntu.iso"
    assert sanitize_iso_name("../../etc/passwd.iso") == "passwd.iso"
    assert sanitize_iso_name("Дистрибутив Астра.iso") == "Distributiv_Astra.iso"
    assert sanitize_iso_name("???.iso") == "image.iso"
    name = unique_iso_file_name("debian-13.iso")
    guid, rest = name.split("_", 1)
    assert str(uuid.UUID(guid)) == guid
    assert rest == "debian-13.iso"
    assert unique_iso_file_name("a.iso") != unique_iso_file_name("a.iso")


def test_guac_encode_and_parse():
    assert encode("select", "rdp") == "6.select,3.rdp;"
    assert encode("", "ping", "123") == "0.,4.ping,3.123;"
    p = InstructionParser()
    out = p.feed("4.sync,3.123;5.mouse,1.1,")
    assert [(o, a) for o, a, _ in out] == [("sync", ["123"])]
    out = p.feed("1.2;3.key,3.a;b,1.1;")
    assert [(o, a) for o, a, _ in out] == [("mouse", ["1", "2"]), ("key", ["a;b", "1"])]
    assert p.buffer == ""
    # Unicode lengths are in characters, not bytes
    out = p.feed(encode("clipboard", "привет") + "4.sy")
    assert out[0][1] == ["привет"]
    assert p.complete_prefix("nc,1.1;") == "4.sync,1.1;"


def test_ps_script_build_and_parse():
    src = build_script("vm_power")
    assert src.startswith("param([string]$ArgsJson)")
    assert "function Get-HvdVm" in src and "'HVDJSON:'" in src
    assert parse_output(['HVDJSON:{"ok":true,"data":{"state":"Running"}}'], [], "h") == {"state": "Running"}
    with pytest.raises(HyperVError) as ei:
        parse_output(['HVDJSON:{"ok":false,"error":"HVD:VM_NOT_FOUND:VM x is not registered"}'], [], "h")
    assert ei.value.code == "VM_NOT_FOUND"
    with pytest.raises(HyperVError) as ei:
        parse_output([], ["boom"], "h")
    assert ei.value.code == "SCRIPT_ERROR"
    # stray pipeline output before the final hashtable
    assert parse_output(['HVDJSON:{"ok":true,"data":["x",{"a":1}]}'], [], "h") == {"a": 1}


class _U:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


def test_rbac_scopes():
    vm1, vm2 = uuid.uuid4(), uuid.uuid4()
    admin = Principal(user=_U(uuid.uuid4()), session=None, tenant=None, assignments=[("tenant_admin", None)])
    assert admin.has_tenant_perm("vm.create") and admin.has_vm_perm("vm.delete", vm1) and admin.sees_all_vms()

    auditor = Principal(user=_U(uuid.uuid4()), session=None, tenant=None, assignments=[("audit", None)])
    assert auditor.has_vm_perm("vm.view", vm1) and not auditor.has_vm_perm("vm.power", vm1)
    assert auditor.has_tenant_perm("event.view") and not auditor.has_tenant_perm("vm.create")

    delegated = Principal(user=_U(uuid.uuid4()), session=None, tenant=None,
                          assignments=[("console", vm1), ("tenant_admin", vm2)])
    assert delegated.has_vm_perm("vm.console", vm1) and not delegated.has_vm_perm("vm.power", vm1)
    assert delegated.has_vm_perm("vm.power", vm2) and delegated.has_vm_perm("vm.console", vm2)
    assert not delegated.sees_all_vms() and delegated.scoped_vm_ids() == {vm1, vm2}
    assert not delegated.has_tenant_perm("vm.create")
    assert delegated.has_tenant_perm("iso.view")  # needed to mount ISO on its VM


def _ps_balanced(text: str) -> str | None:
    """Rough PowerShell sanity check: brackets balanced outside strings/comments."""
    pairs = {")": "(", "}": "{", "]": "["}
    stack: list[tuple[str, int]] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "'":
            i += 1
            while i < n and not (text[i] == "'" and text[i + 1:i + 2] != "'"):
                i += 2 if text[i] == "'" else 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "`" else 1
        elif c in "({[":
            stack.append((c, line))
        elif c in ")}]":
            if not stack or stack[-1][0] != pairs[c]:
                return f"unbalanced {c} at line {line}"
            stack.pop()
        i += 1
    return f"unclosed {stack[-1]}" if stack else None


def test_ps_scripts_wellformed():
    from hvdirector.hyperv.psrp import SCRIPTS_DIR

    names = [p.stem for p in SCRIPTS_DIR.glob("*.ps1") if not p.name.startswith("_")]
    assert len(names) >= 15
    for name in names:
        src = build_script(name)
        assert _ps_balanced(src) is None, (name, _ps_balanced(src))
        assert "$ArgsJson" in src and "return" in src
