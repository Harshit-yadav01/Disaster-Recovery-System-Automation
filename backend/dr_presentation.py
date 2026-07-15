r"""Read-only validation for the present-to-host discovery (Step 1).

Runs ``showhost`` and ``showvlun`` on the recovery (DR) and primary arrays over
SSH, prints the RAW CLI output alongside what the parsers extracted, so we can
confirm the parsers match your real array output before any write workflow is
built on top. NOTHING is changed on either array.

Usage (from backend/, venv active):
    .\.venv\Scripts\python.exe dr_presentation.py            # DR array
    .\.venv\Scripts\python.exe dr_presentation.py --primary  # primary array
    .\.venv\Scripts\python.exe dr_presentation.py --lunmap   # primary LUN map
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only present-to-host discovery.")
    ap.add_argument("--primary", action="store_true", help="Use the primary array instead of DR.")
    ap.add_argument("--lunmap", action="store_true", help="Print the primary VV->LUN map.")
    args = ap.parse_args()

    s = get_settings()
    role = "primary" if args.primary else "recovery"
    host = (s.alletra_primary_base_url or s.alletra_base_url) if args.primary else s.alletra_recovery_base_url
    if not host:
        print(f"No {role} array configured in .env.", file=sys.stderr)
        return 2

    print(f"==== {role.upper()} array: {SSHConfig.clean_host(host)} ====\n")

    try:
        if args.lunmap:
            print("Primary VV -> LUN map (from showvlun -a on primary):")
            for vv, lun in sorted(wf.primary_lun_map(s).items()):
                print(f"  {vv:<40} LUN {lun}")
            return 0

        for cmd, parser, label in (
            ("showhost", parse_showhost, "HOSTS"),
            ("showvlun -a", parse_showvlun, "ACTIVE VLUNS"),
        ):
            raw = _run(s, host, role, cmd)
            print(f"----- RAW: {cmd} -----")
            print(raw)
            print(f"----- PARSED: {label} -----")
            parsed = parser(raw)
            if not parsed:
                print("  (parser found 0 rows - layout may differ; paste the RAW above so it can be fixed)")
            for item in parsed:
                print("  " + str(dataclasses.asdict(item)))
            print()
    except SSHError as exc:
        print(f"SSH error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
