r"""Read-only DR status check over SSH (step 1).

Logs into the Primary and (if configured) Secondary array over SSH, runs the
read-only ``showrcopy`` command, parses it, and prints the Remote Copy state:
group name (per array), role, started/stopped, and per-volume SyncStatus.

SAFE: this changes nothing on either array. It only runs ``showrcopy``.

Usage (from the backend/ folder, venv active):

    .\.venv\Scripts\python.exe dr_status.py
    .\.venv\Scripts\python.exe dr_status.py --group Intern_Automation
    .\.venv\Scripts\python.exe dr_status.py --host 10.64.122.99
"""
from __future__ import annotations

import argparse
import os
import sys

# Talk to the internal arrays directly - strip any corporate proxy from this
# process so nothing tries to route the internal IP through it.
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

from app.config import get_settings  # noqa: E402
from app.dr.showrcopy import RcopyStatus, parse_showrcopy  # noqa: E402
from app.dr.ssh_client import ArraySSH, SSHConfig, SSHError  # noqa: E402


def _targets(settings, host_override: str | None) -> list[tuple[str, str]]:
    """Return a list of (role_label, host) to query."""
    if host_override:
        return [("array", host_override)]
    primary = settings.alletra_primary_base_url or settings.alletra_base_url
    recovery = settings.alletra_recovery_base_url
    out: list[tuple[str, str]] = []
    if primary:
        out.append(("primary", primary))
    if recovery:
        out.append(("recovery", recovery))
    return out


def _print_status(role_label: str, host: str, status: RcopyStatus, group_filter: str | None) -> None:
    clean = SSHConfig.clean_host(host)
    print(f"\n=== {role_label.upper()} array  {clean} ===")
    print(f"  System: {status.system_status or 'unknown'}")
    groups = status.groups
    if group_filter:
        g = status.find_group(group_filter)
        groups = [g] if g else []
    if not groups:
        print("  No Remote Copy groups found"
              + (f" matching '{group_filter}'." if group_filter else "."))
        return
    for g in groups:
        synced = "yes" if g.all_synced() else "no"
        print(f"  Group : {g.name}")
        print(f"    Target : {g.target}")
        print(f"    Role   : {g.role}    Status: {g.status}    Mode: {g.mode}")
        print(f"    AllSynced: {synced}  ({len(g.volumes)} volume(s))")
        for v in g.volumes:
            print(f"      - {v.local_vv} -> {v.remote_vv} : {v.sync_status}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dr_status.py",
        description="Read-only Remote Copy status over SSH (runs showrcopy).",
    )
    parser.add_argument("--host", help="Query a single array IP/host (overrides .env)")
    parser.add_argument("--group", help="Only show this Remote Copy group (base name)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.alletra_username or not settings.alletra_password:
        print("ALLETRA_USERNAME / ALLETRA_PASSWORD are not set in .env.", file=sys.stderr)
        return 2

    targets = _targets(settings, args.host)
    if not targets:
        print("No array configured. Set ALLETRA_PRIMARY_BASE_URL in .env or pass --host.",
              file=sys.stderr)
        return 2

    rc = 0
    for role_label, host in targets:
        cfg = SSHConfig(
            host=host,
            username=settings.alletra_username,
            password=settings.alletra_password,
            port=settings.alletra_ssh_port,
            timeout=settings.alletra_timeout,
            role=role_label,
        )
        try:
            with ArraySSH(cfg) as arr:
                text = arr.run("showrcopy")
            status = parse_showrcopy(text)
            _print_status(role_label, host, status, args.group)
        except SSHError as exc:
            print(f"\n=== {role_label.upper()} array  {SSHConfig.clean_host(host)} ===")
            print(f"  UNREACHABLE / SSH error: {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
