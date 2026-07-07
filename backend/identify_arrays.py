"""Identify which array is the replication SOURCE (primary) vs TARGET (recovery).

Usage (from the backend folder, venv active):

    .\.venv\Scripts\python.exe identify_arrays.py <IP_A> <IP_B>

It logs into each array over WSAPI (port 443) using the ALLETRA_USERNAME /
ALLETRA_PASSWORD from your .env, then reads:

  * /api/v1/system            -> array name, model, serial
  * /api/v1/remotecopygroups  -> Remote Copy group roles

In HPE 3PAR/Alletra Remote Copy the SOURCE array's groups have role "Primary"
and the TARGET array's groups have role "Secondary". Whichever array reports
role=Primary is the one to set as ALLETRA_PRIMARY_BASE_URL; the other is
ALLETRA_RECOVERY_BASE_URL.

Read-only: it only logs in, GETs, and logs out. It never prints the password.
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from app.config import get_settings

# WSAPI remote-copy group role enum (3PAR/Alletra WSAPI).
ROLE_NAMES = {1: "Primary", 2: "Secondary", 3: "PrimaryRev", 4: "SecondaryRev"}
SOURCE_ROLES = {1, 3}  # these mark the replication SOURCE array


async def inspect(host: str, user: str, password: str, timeout: int) -> dict:
    """Log into one array and return its identity + remote-copy roles."""
    base = f"https://{host}:443"
    result: dict = {"host": host, "reachable": False, "error": None,
                    "name": None, "model": None, "serial": None,
                    "roles": [], "is_source": False}
    try:
        # trust_env=False so the internal array is reached directly, not via
        # the corporate proxy (same fix used by the app provider).
        async with httpx.AsyncClient(
            base_url=base, verify=False, timeout=timeout, trust_env=False
        ) as client:
            login = await client.post(
                "/api/v1/credentials", json={"user": user, "password": password}
            )
            login.raise_for_status()
            key = login.json().get("key")
            if not key:
                result["error"] = "no session key returned"
                return result
            headers = {"X-HP3PAR-WSAPI-SessionKey": key, "Accept": "application/json"}

            sys_info = (await client.get("/api/v1/system", headers=headers)).json()
            result["reachable"] = True
            result["name"] = sys_info.get("name")
            result["model"] = sys_info.get("model")
            result["serial"] = sys_info.get("serialNumber") or sys_info.get("systemVersion")

            rc = (await client.get("/api/v1/remotecopygroups", headers=headers)).json()
            roles = []
            for g in rc.get("members", []):
                role = g.get("role")
                roles.append((g.get("name"), role, ROLE_NAMES.get(role, f"role={role}")))
                if role in SOURCE_ROLES:
                    result["is_source"] = True
            result["roles"] = roles

            await client.delete(f"/api/v1/credentials/{key}", headers=headers)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python identify_arrays.py <IP_A> <IP_B>")
        raise SystemExit(2)

    s = get_settings()
    if not s.alletra_username or not s.alletra_password:
        print("ALLETRA_USERNAME / ALLETRA_PASSWORD not set in .env - set them first.")
        raise SystemExit(2)

    hosts = [sys.argv[1], sys.argv[2]]
    print(f"Logging into: {hosts[0]} and {hosts[1]} as '{s.alletra_username}'")
    print("=" * 60)

    results = await asyncio.gather(
        *(inspect(h, s.alletra_username, s.alletra_password, s.alletra_timeout) for h in hosts)
    )

    for r in results:
        print(f"\nArray {r['host']}")
        if not r["reachable"]:
            print(f"  UNREACHABLE / login failed - {r['error']}")
            continue
        print(f"  Name   : {r['name']}")
        print(f"  Model  : {r['model']}")
        print(f"  Serial : {r['serial']}")
        if r["roles"]:
            print("  Remote Copy groups:")
            for name, _code, label in r["roles"]:
                print(f"     - {name}: {label}")
        else:
            print("  Remote Copy groups: none found")

    print("\n" + "=" * 60)
    sources = [r for r in results if r["is_source"]]
    targets = [r for r in results if r["reachable"] and not r["is_source"]]
    if len(sources) == 1 and targets:
        print(f"SOURCE  (set as ALLETRA_PRIMARY_BASE_URL) : {sources[0]['host']}")
        print(f"TARGET  (set as ALLETRA_RECOVERY_BASE_URL): {targets[0]['host']}")
    elif not sources:
        print("Could not determine a source: no Remote Copy group with a Primary role")
        print("was found on either array. Check that replication is configured.")
    else:
        print("Both arrays report a Primary role - they may host independent groups.")
        print("Pick the production array as ALLETRA_PRIMARY_BASE_URL.")


if __name__ == "__main__":
    asyncio.run(main())
