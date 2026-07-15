r"""Present-to-host CLI: export/remove the DR group's volumes to a DR host.

DRY-RUN by default (prints the exact createvlun/removevlun commands without
running them). Pass --execute to actually run them; a typed confirmation is
required for --execute unless --yes is given.

Scoped to ONE Remote Copy group (default: Intern_Automation) and only ever acts
on that group's own volumes.

Usage (from backend/, venv active):
    .\.venv\Scripts\python.exe dr_present.py present --host TPIPoc-esxi1            # dry-run preview
    .\.venv\Scripts\python.exe dr_present.py present --host TPIPoc-esxi1 --execute
    .\.venv\Scripts\python.exe dr_present.py unpresent --host TPIPoc-esxi1          # dry-run preview
    .\.venv\Scripts\python.exe dr_present.py unpresent --host TPIPoc-esxi1 --execute
"""
from __future__ import annotations

import argparse
import os
import sys

for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
             "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

from app.config import get_settings  # noqa: E402
from app.dr.workflows import (  # noqa: E402
    DEFAULT_GROUP,
    DrError,
    StepResult,
    present_to_host,
    unpresent_from_host,
)


def _print_steps(steps: list[StepResult]) -> None:
    for s in steps:
        mark = "OK " if s.ok else "XX "
        line = f"  [{mark}] {s.name}"
        if s.command:
            line += f"  $ {s.command}"
        if s.detail:
            line += f"\n         {s.detail}"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Present-to-host CLI (dry-run by default).")
    ap.add_argument("action", choices=["present", "unpresent"])
    ap.add_argument("--group", default=DEFAULT_GROUP, help="RC group (default: Intern_Automation).")
    ap.add_argument("--host", default=None, help="Host or 'set:<hostset>' to present to (overrides DR_HOST_TARGET).")
    ap.add_argument("--execute", action="store_true", help="Actually run (default is dry-run).")
    ap.add_argument("--yes", action="store_true", help="Skip the typed confirmation for --execute.")
    args = ap.parse_args()

    s = get_settings()
    dry = not args.execute

    if args.execute and not args.yes:
        word = args.action
        typed = input(f"Type '{word}' to actually run it on the DR array: ").strip()
        if typed != word:
            print("Aborted (confirmation did not match).")
            return 1

    fn = present_to_host if args.action == "present" else unpresent_from_host
    print(f"\n== {args.action.upper()} group '{args.group}'"
          f"{' (DRY-RUN)' if dry else ' (EXECUTE)'} ==")
    try:
        steps = fn(s, args.group, host_target=args.host, dry_run=dry)
    except DrError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_steps(steps)
    failed = [x for x in steps if not x.ok and x.name != "precondition"]
    return 1 if (not dry and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
