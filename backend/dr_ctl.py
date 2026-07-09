r"""DR control CLI - chunk 1: status + start/stop/sync replication link ops.

Scoped to ONE Remote Copy group (default: Intern_Automation). State-changing
operations are DRY-RUN by default; pass --execute to actually run them. A typed
confirmation is required for --execute unless --yes is given.

SAFE by design: only ever targets the named group, never all groups, and always
runs on whichever array currently holds the group as Primary (discovered live).

Usage (from backend/, venv active):
    .\.venv\Scripts\python.exe dr_ctl.py status
    .\.venv\Scripts\python.exe dr_ctl.py sync                 # dry-run preview
    .\.venv\Scripts\python.exe dr_ctl.py sync --execute
    .\.venv\Scripts\python.exe dr_ctl.py stop --execute
    .\.venv\Scripts\python.exe dr_ctl.py start --execute
"""
from __future__ import annotations

import argparse
import os
import sys

# Talk to the internal arrays directly - strip any corporate proxy from this
# process so nothing routes an internal IP through it.
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

from app.config import get_settings  # noqa: E402
from app.dr.ssh_client import SSHConfig  # noqa: E402
from app.dr.workflows import (  # noqa: E402
    DEFAULT_GROUP,
    DrError,
    StepResult,
    gather_status,
    resolve_primary,
    run_link_op,
)


def _print_status(settings, base_group: str) -> int:
    views = gather_status(settings, base_group)
    if not views:
        print("No array configured. Set ALLETRA_PRIMARY_BASE_URL in .env.", file=sys.stderr)
        return 2
    for v in views:
        clean = SSHConfig.clean_host(v.host)
        print(f"\n=== {v.role_label.upper()} array  {clean} ===")
        print(f"  System: {v.status.system_status or 'unknown'}")
        if v.group is None:
            print(f"  Group '{base_group}': not found / unreachable")
            continue
        g = v.group
        print(f"  Group : {g.name}")
        print(f"    Target : {g.target}")
        print(f"    Role   : {g.role}    Status: {g.status}    Mode: {g.mode}")
        print(f"    AllSynced: {'yes' if g.all_synced() else 'no'}  ({len(g.volumes)} volume(s))")
        for vol in g.volumes:
            print(f"      - {vol.local_vv} -> {vol.remote_vv} : {vol.sync_status}")
    return 0


def _print_results(op: str, results: list[StepResult]) -> int:
    print(f"\n{op.upper()} results:")
    failed = 0
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        if not r.ok:
            failed += 1
        print(f"  [{mark}] {r.name}: {r.detail}")
        if r.command and r.command != "showrcopy":
            print(f"         command: {r.command}")
    return 1 if failed else 0


def _confirm(op: str, settings, base_group: str, assume_yes: bool) -> bool:
    try:
        host, group_name, group = resolve_primary(settings, base_group)
    except DrError as exc:
        print(f"Cannot proceed: {exc}", file=sys.stderr)
        return False
    clean = SSHConfig.clean_host(host)
    print("=" * 64)
    print(f"  ABOUT TO {op.upper()} group '{group_name}' on {clean}")
    print(f"  Current: role={group.role}, status={group.status}, "
          f"synced={group.all_synced()}")
    print("  This changes live replication state.")
    print("=" * 64)
    if assume_yes:
        return True
    answer = input(f"  Type '{op}' to proceed (anything else aborts): ").strip()
    return answer == op


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dr_ctl.py",
        description="DR replication control (status + start/stop/sync) for one RCG.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show Remote Copy status (read-only)")
    p_status.add_argument("--group", default=DEFAULT_GROUP, help="Group base name")

    for op in ("start", "stop", "sync"):
        sp = sub.add_parser(op, help=f"{op} replication for the group")
        sp.add_argument("--group", default=DEFAULT_GROUP, help="Group base name")
        sp.add_argument("--execute", action="store_true",
                        help="Actually run it (omit for a dry-run preview)")
        sp.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt")
        sp.add_argument("--timeout", type=int, default=180,
                        help="Seconds to wait for the expected state (default 180)")

    args = parser.parse_args()

    settings = get_settings()
    if not settings.alletra_username or not settings.alletra_password:
        print("ALLETRA_USERNAME / ALLETRA_PASSWORD are not set in .env.", file=sys.stderr)
        return 2

    if args.command == "status":
        return _print_status(settings, args.group)

    op = args.command
    dry_run = not args.execute

    if not dry_run:
        if not _confirm(op, settings, args.group, args.yes):
            print("Aborted - no changes made.")
            return 1

    try:
        results = run_link_op(
            settings, op, args.group, dry_run=dry_run, timeout=args.timeout
        )
    except DrError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rc = _print_results(op, results)
    if dry_run:
        print("\n(DRY-RUN: nothing was changed. Re-run with --execute to apply.)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
