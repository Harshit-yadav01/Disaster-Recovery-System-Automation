"""Temporary WSAPI connectivity diagnostic. Safe: read-only, prints no password."""
import asyncio
import socket
import ssl

import httpx

from app.config import get_settings

s = get_settings()
host = s.alletra_primary_base_url
user = s.alletra_username
CANDIDATE_PORTS = [443, 8080, 8443, 5783]

print(f"Target host : {host}")
print(f"Username    : {user}")
print("=" * 56)


def tcp_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(6)
    try:
        sock.connect((host, port))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        sock.close()


def tls_probe(port: int) -> str:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = socket.create_connection((host, port), timeout=6)
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            return f"TLS OK ({tls.version()})"
    except Exception as exc:  # noqa: BLE001
        return f"TLS FAIL - {type(exc).__name__}: {exc}"


def http_probe(port: int) -> str:
    """Is it plain HTTP (not TLS)?"""
    try:
        r = httpx.get(f"http://{host}:{port}/api/v1/system", timeout=6)
        return f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"HTTP FAIL - {type(exc).__name__}"


async def wsapi_login(scheme: str, port: int) -> str:
    try:
        async with httpx.AsyncClient(
            base_url=f"{scheme}://{host}:{port}", verify=False, timeout=12
        ) as client:
            resp = await client.post(
                "/api/v1/credentials",
                json={"user": user, "password": s.alletra_password},
            )
            if resp.status_code == 201 and resp.json().get("key"):
                return f"LOGIN OK (HTTP 201, key received) via {scheme}:{port}"
            return f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL - {type(exc).__name__}: {exc!r}"


async def main() -> None:
    for port in CANDIDATE_PORTS:
        print(f"\n--- port {port} ---")
        if not tcp_open(port):
            print("  TCP  : closed / unreachable")
            continue
        print("  TCP  : open")
        print(f"  TLS  : {tls_probe(port)}")
        print(f"  HTTP : {http_probe(port)}")
        print(f"  HTTPS login: {await wsapi_login('https', port)}")
        print(f"  HTTP  login: {await wsapi_login('http', port)}")


asyncio.run(main())
