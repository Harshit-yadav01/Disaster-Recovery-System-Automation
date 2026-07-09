"""DR automation CLI for HPE Alletra / 3PAR Remote Copy.

Drives the replication lifecycle against the SECONDARY (target) array:

    status    - read-only: list Remote Copy groups + roles (safe, no changes)
    failover  - promote secondary to primary (R/W); optionally present volumes
    recover   - reverse-replicate secondary -> original primary
    restore   - return replication to the original direction

Safety model
------------
* DRY-RUN by default. Nothing is changed unless you pass --execute.
* Destructive phases (failover/recover/restore) require typed confirmation
  unless --yes is given.
* auto_synchronize is never enabled - each phase is explicit (customer req).
* No health check is performed on the primary before failover - the operator
  must confirm the primary site is failed/inaccessible first.

Examples
--------
    # See the groups and their roles (safe)
    python dr_automation.py status

    # Preview a failover of all groups (dry-run)
    python dr_automation.py failover

    # Actually fail over and present volumes to a secondary-site host
    python dr_automation.py failover --execute --present-host esx-dr-01

    # Reverse-replicate back, then restore original direction
    python dr_automation.py recover --execute
    python dr_automation.py restore --execute
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# The DR tool only ever talks to the INTERNAL arrays, so strip any corporate
# proxy from this process's environment. Otherwise the underlying HTTP client
# would route the internal array IP through the proxy and time out (same class
# of issue fixed for the dashboard's httpx client).
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

from app.config import get_settings  # noqa: E402
from app.dr import DrManager  # noqa: E402


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("dr_automation.log", encoding="utf-8"),
        ],
    )


def _parse_groups(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [g.strip() for g in value.split(",") if g.strip()]


def _confirm(phase: str, host: str, groups: list[str] | None, assume_yes: bool) -> bool:
    target = ", ".join(groups) if groups else "ALL Remote Copy groups"
    print()
    print("=" * 64)
    print(f"  ABOUT TO {phase.upper()} on array {host}")
    print(f"  Groups : {target}")
    print("  This changes live replication state and can affect data.")
    print("=" * 64)
    if assume_yes:
        return True
    token = phase.upper()
    answer = input(f"  Type '{token}' to proceed (anything else aborts): ").strip()
    return answer == token


def _print_results(phase: str, results: list) -> int:
    ok = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    print(f"\n{phase}: {ok}/{len(results)} succeeded")
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        print(f"  [{mark}] {r.name}: {r.detail}")
    return 1 if failed else 0


def _host_for(args, settings) -> str:
    # Actions run against the SECONDARY/target array by default.
    host = args.host or settings.alletra_recovery_base_url or settings.alletra_primary_base_url
    if not host:
        print("No target array configured. Set ALLETRA_RECOVERY_BASE_URL in .env "
              "or pass --host.", file=sys.stderr)
        raise SystemExit(2)
    return host


def cmd_status(args, settings) -> int:
    host = _host_for(args, settings)
    with DrManager(host, settings.alletra_username, settings.alletra_password,
                   timeout=settings.alletra_timeout, dry_run=True) as dr:
        groups = dr.list_groups()
        if not groups:
            print(f"No Remote Copy groups found on {host}.")
            return 0
        print(f"\nRemote Copy groups on {host}:")
        for g in groups:
            role = g.get("role")
            role_name = {1: "Primary", 2: "Secondary"}.get(role, f"role={role}")
            vols = [v.get("localVolumeName") or v.get("name")
                    for v in g.get("volumes", [])]
            print(f"  - {g.get('name')}: {role_name}, {len(vols)} volume(s)")
    return 0


def _run_phase(phase: str, args, settings) -> int:
    host = _host_for(args, settings)
    groups = _parse_groups(args.groups)
    dry_run = not args.execute

    if not dry_run and not _confirm(phase, host, groups, args.yes):
        print("Aborted - no changes made.")
        return 1

    with DrManager(host, settings.alletra_username, settings.alletra_password,
                   timeout=settings.alletra_timeout, dry_run=dry_run) as dr:
        rc = 0
        # A planned failover needs the group stopped first (the array rejects
        # failover on an actively-replicating group). --stop-first does that.
        if phase == "failover" and getattr(args, "stop_first", False):
            stop_results = dr.stop(groups=groups)
            rc |= _print_results("stop", stop_results)

        results = getattr(dr, phase)(groups=groups)
        rc |= _print_results(phase, results)

        # Failover optionally presents the secondary volumes to a host.
        if phase == "failover" and args.present_host:
            pres = dr.present_group_volumes(args.present_host, groups=groups)
            rc |= _print_results("present-volumes", pres)

    if dry_run:
        print("\n(DRY-RUN: nothing was changed. Re-run with --execute to apply.)")
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dr_automation.py",
        description="HPE Alletra/3PAR Remote Copy DR automation "
                    "(failover / recover / restore).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--host", help="Target array IP/host (default: recovery array from .env)")
        sp.add_argument("--groups", help="Comma-separated Remote Copy group names (default: all)")

    sp = sub.add_parser("status", help="List Remote Copy groups and roles (read-only)")
    add_common(sp)

    for phase in ("failover", "recover", "restore", "stop", "start"):
        sp = sub.add_parser(phase, help=f"{phase.capitalize()} Remote Copy groups")
        add_common(sp)
        sp.add_argument("--execute", action="store_true",
                        help="Actually perform the action (omit for a dry-run)")
        sp.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation prompt")
        if phase == "failover":
            sp.add_argument("--stop-first", action="store_true",
                            help="Stop the group before failover (needed for a "
                                 "planned failover on a running group)")
            sp.add_argument("--present-host",
                            help="After failover, present the group volumes to this "
                                 "host at the secondary site")
    return p


def main() -> int:
    _configure_logging()
    args = build_parser().parse_args()
    settings = get_settings()

    if not settings.alletra_username or not settings.alletra_password:
        print("ALLETRA_USERNAME / ALLETRA_PASSWORD are not set in .env.", file=sys.stderr)
        return 2

    if args.command == "status":
        return cmd_status(args, settings)
    return _run_phase(args.command, args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
