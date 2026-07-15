r"""Read-only present-to-host discovery, SCOPED to one Remote Copy group.

By default prints a SHORT report for the group (default: Intern_Automation):
  * the group's volumes on the PRIMARY, their LUN, and the host they're on,
  * the group's volumes on the DR array and whether they're already exported,
  * the DR array's VMware hosts (candidate present-to-host targets).
NOTHING is changed on either array.

Usage (from backend/, venv active):
    .\.venv\Scripts\python.exe dr_presentation.py                 # scoped report
    .\.venv\Scripts\python.exe dr_presentation.py --group NAME    # different group
    .\.venv\Scripts\python.exe dr_presentation.py --raw           # full raw dump (DR)
    .\.venv\Scripts\python.exe dr_presentation.py --raw --primary # full raw dump (primary)
    .\.venv\Scripts\python.exe dr_presentation.py --lunmap        # full primary LUN map
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys

# Talk to the internal arrays directly - strip any corporate proxy.
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

from app.config import get_settings  # noqa: E402
from app.dr.ssh_client import ArraySSH, SSHConfig, SSHError  # noqa: E402
from app.dr.showvlun import parse_showhost, parse_showvlun  # noqa: E402
from app.dr import workflows as wf  # noqa: E402


def _cfg(settings, host, role):
    return SSHConfig(
        host=host, username=settings.alletra_username,
        password=settings.alletra_password, port=settings.alletra_ssh_port,
        timeout=settings.alletra_timeout, role=role,
    )


def _run(settings, host, role, command):
    with ArraySSH(_cfg(settings, host, role)) as arr:
        return arr.run(command)


def _scoped_report(s, group: str) -> int:
    # 1) Group volumes on each array (from showrcopy via gather_status).
    views = wf.gather_status(s, group)
    prim = next((v for v in views if v.role_label == "primary"), None)
    dr = next((v for v in views if v.role_label == "recovery"), None)
    prim_vols = [vol.local_vv for vol in prim.group.volumes] if prim and prim.group else []
    dr_vols = [vol.local_vv for vol in dr.group.volumes] if dr and dr.group else []

    print(f"==== Group '{group}' present-to-host report ====\n")
    print(f"Primary array : {SSHConfig.clean_host(prim.host) if prim else '-'}"
          f"   group volumes: {len(prim_vols)}")
    print(f"DR array      : {SSHConfig.clean_host(dr.host) if dr else '-'}"
          f"   group volumes: {len(dr_vols)}\n")

    if prim and prim_vols:
        try:
            pex = [v for v in wf.list_exports(s, "primary") if v.vv_name in set(prim_vols)]
            print("-- PRIMARY exports for this group (LUN/host it uses today) --")
            if not pex:
                print("   (none found - volumes may not be exported on the primary)")
            for v in pex:
                print(f"   VV {v.vv_name:<32} LUN {str(v.lun):<5} host {v.host_name:<20} port {v.port}")
            print()
        except SSHError as exc:
            print(f"   primary showvlun error: {exc}\n")

    if dr and dr_vols:
        try:
            dex = [v for v in wf.list_exports(s, "recovery") if v.vv_name in set(dr_vols)]
            print("-- DR exports for this group (already presented?) --")
            if not dex:
                print("   (none - group volumes are NOT yet presented on the DR array)")
            for v in dex:
                print(f"   VV {v.vv_name:<32} LUN {str(v.lun):<5} host {v.host_name:<20} port {v.port}")
            print()
        except SSHError as exc:
            print(f"   DR showvlun error: {exc}\n")

    if dr:
        try:
            vmware = [h for h in wf.list_hosts(s, "recovery") if h.is_vmware]
            print("-- DR array VMware hosts (candidate targets) --")
            if not vmware:
                print("   (no VMware-persona hosts found; run with --raw to see all hosts)")
            for h in vmware:
                print(f"   [{h.id}] {h.name:<24} persona={h.persona}  paths={len(h.wwns)}")
            print()
        except SSHError as exc:
            print(f"   DR showhost error: {exc}\n")

    print("Tip: present-to-host will export ONLY the group volumes above, to the host you pick.")
    return 0


def _raw_dump(s, primary: bool) -> int:
    role = "primary" if primary else "recovery"
    host = (s.alletra_primary_base_url or s.alletra_base_url) if primary else s.alletra_recovery_base_url
    if not host:
        print(f"No {role} array configured in .env.", file=sys.stderr)
        return 2
    print(f"==== RAW {role.upper()} array: {SSHConfig.clean_host(host)} ====\n")
    for cmd, parser, label in (("showhost", parse_showhost, "HOSTS"),
                               ("showvlun -a", parse_showvlun, "ACTIVE VLUNS")):
        raw = _run(s, host, role, cmd)
        print(f"----- RAW: {cmd} -----\n{raw}\n----- PARSED: {label} ({len(parser(raw))} rows) -----")
        for item in parser(raw):
            print("  " + str(dataclasses.asdict(item)))
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only present-to-host discovery (scoped).")
    ap.add_argument("--group", default=wf.DEFAULT_GROUP, help="RC group to report on.")
    ap.add_argument("--raw", action="store_true", help="Full raw+parsed dump instead of scoped report.")
    ap.add_argument("--primary", action="store_true", help="With --raw: dump the primary array.")
    ap.add_argument("--lunmap", action="store_true", help="Print the full primary VV->LUN map.")
    args = ap.parse_args()

    s = get_settings()
    try:
        if args.lunmap:
            print("Primary VV -> LUN map:")
            for vv, lun in sorted(wf.primary_lun_map(s).items()):
                print(f"  {vv:<40} LUN {lun}")
            return 0
        if args.raw:
            return _raw_dump(s, args.primary)
        return _scoped_report(s, args.group)
    except SSHError as exc:
        print(f"SSH error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
