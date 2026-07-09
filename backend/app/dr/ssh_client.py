"""Read-only SSH transport for the HPE Alletra / 3PAR CLI.

Connects to an array over SSH (port 22) as the configured user and runs CLI
commands, returning clean text output. Two execution modes are supported and
**auto-detected** on first use, because 3PAR/Alletra SSH sometimes accepts a
one-shot ``exec_command`` and sometimes only offers an interactive ``cli%``
shell:

  * ``exec``  - run the command directly, read stdout.
  * ``shell`` - open an interactive shell, send the command, read until the
                next ``cli%`` prompt, and strip the echoed command + prompt.

Step 1 only ever runs read-only commands (``showrcopy``). No state is changed.

The client connects **directly** to the internal management IP; it does not use
the jumpbox's HTTP(S) proxy (that only applies to HTTP clients, but we also set
``look_for_keys=False`` / ``allow_agent=False`` to keep auth deterministic).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

try:
    import paramiko
except ImportError as _exc:  # pragma: no cover - surfaced only when SSH is used
    paramiko = None
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None

logger = logging.getLogger("dr.ssh")

# Matches the trailing interactive CLI prompt, e.g. "AlletraMP_D22U27 cli% ".
_PROMPT_RE = re.compile(r"cli%\s*$")


class SSHError(RuntimeError):
    """Raised when the array cannot be reached, authenticated, or a command fails."""


@dataclass
class SSHConfig:
    """Connection details for one array's SSH endpoint."""

    host: str
    username: str
    password: str
    port: int = 22
    timeout: int = 20
    role: str = ""  # "primary" | "recovery" (label only)

    @staticmethod
    def clean_host(raw: str) -> str:
        """Accept an IP, hostname, or URL and return just the host."""
        raw = raw.strip().rstrip("/")
        for prefix in ("https://", "http://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        return raw.split("/")[0].split(":")[0]


class ArraySSH:
    """SSH session to a single array. Use as a context manager::

        with ArraySSH(cfg) as arr:
            text = arr.run("showrcopy")
    """

    def __init__(self, cfg: SSHConfig) -> None:
        if paramiko is None:  # pragma: no cover
            raise SSHError(
                "paramiko is not installed. Run: pip install -r requirements.txt"
            ) from _IMPORT_ERROR
        self.cfg = cfg
        self.host = SSHConfig.clean_host(cfg.host)
        self._client: "paramiko.SSHClient | None" = None
        self._shell = None  # persistent interactive channel (shell mode)
        self._mode: str | None = None  # None -> undetected, then "exec"/"shell"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "ArraySSH":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.cfg.port,
                username=self.cfg.username,
                password=self.cfg.password,
                timeout=self.cfg.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as exc:  # noqa: BLE001 - normalize to SSHError
            raise SSHError(f"SSH connect to {self.host}:{self.cfg.port} failed: {exc}") from exc
        self._client = client
        logger.info("SSH connected to %s (%s)", self.host, self.cfg.role or "array")
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            if self._shell is not None:
                self._shell.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # Command execution (mode auto-detected)
    # ------------------------------------------------------------------ #
    def run(self, command: str) -> str:
        """Run a CLI command and return its cleaned text output."""
        if self._client is None:  # pragma: no cover
            raise SSHError("SSH session is not open")

        if self._mode == "shell":
            return self._run_shell(command)

        # Mode is unknown or already "exec": try exec first.
        out = ""
        try:
            out, err, rc = self._run_exec(command)
        except Exception as exc:  # noqa: BLE001
            if self._mode == "exec":
                raise SSHError(f"exec '{command}' failed on {self.host}: {exc}") from exc
            out = ""

        if self._mode is None:
            if out.strip():
                self._mode = "exec"
                logger.info("SSH %s using exec mode", self.host)
            else:
                self._mode = "shell"
                logger.info("SSH %s falling back to interactive shell mode", self.host)
                return self._run_shell(command)

        return out

    def _run_exec(self, command: str) -> tuple[str, str, int | None]:
        assert self._client is not None
        stdin, stdout, stderr = self._client.exec_command(command, timeout=self.cfg.timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return out, err, rc

    def _run_shell(self, command: str) -> str:
        assert self._client is not None
        if self._shell is None:
            self._shell = self._client.invoke_shell(width=1000, height=1000)
            # Consume the banner and the first prompt.
            self._read_until_prompt()
        self._shell.send(command + "\n")
        raw = self._read_until_prompt()
        return self._clean_shell_output(raw, command)

    def _read_until_prompt(self) -> str:
        """Read from the interactive channel until the cli% prompt or timeout."""
        assert self._shell is not None
        buf: list[str] = []
        deadline = time.time() + self.cfg.timeout
        while time.time() < deadline:
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535).decode("utf-8", errors="replace")
                buf.append(chunk)
                if _PROMPT_RE.search("".join(buf)):
                    break
            else:
                time.sleep(0.1)
                if self._shell.exit_status_ready() and not self._shell.recv_ready():
                    break
        return "".join(buf)

    @staticmethod
    def _clean_shell_output(raw: str, command: str) -> str:
        """Strip the echoed command line and the trailing prompt line."""
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        # Drop the echoed command (first line containing it).
        if lines and command in lines[0]:
            lines = lines[1:]
        # Drop trailing prompt line(s).
        while lines and _PROMPT_RE.search(lines[-1]):
            lines = lines[:-1]
        return "\n".join(lines).strip("\n")
