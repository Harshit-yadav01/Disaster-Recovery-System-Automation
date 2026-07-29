"""Parsers for HPE Alletra / 3PAR performance & health CLI output.

Turns the raw text of read-only stat/show commands into structured values so the
dashboard can display real, live numbers instead of synthetic ones. Pure
standard-library (no third-party deps) so every parser is unit-testable without a
venv or a live array.

Commands covered:
  * ``showalert -n``        -> recent alerts (blocks of Key: Value)
  * ``showcpg -space``      -> per-CPG capacity (MiB table)
  * ``statcpu -iter 1``     -> per-node CPU busy %
  * ``statvv -iter 1``      -> aggregate volume IOPS / throughput / latency
  * ``showrcopy -d groups`` -> remote-copy group status + last-sync (RPO)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "AlertItem",
    "CpgSpace",
    "CpuStats",
    "VvPerf",
    "RcGroupSummary",
    "parse_showalert",
    "parse_showcpg_space",
    "parse_statcpu",
    "parse_statvv",
    "parse_showrcopy_groups",
    "severity_tone",
    "human_mib",
]

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _to_int(token: str) -> int | None:
    try:
        return int(token)
    except (ValueError, TypeError):
        return None


def _to_float(token: str) -> float | None:
    try:
        return float(token)
    except (ValueError, TypeError):
        return None


def severity_tone(severity: str) -> str:
    """Map an array alert severity to a UI tone (red / warning / blue)."""
    s = severity.strip().lower()
    if s in ("fatal", "critical", "major"):
        return "red"
    if s in ("degraded", "minor"):
        return "warning"
    return "blue"


def human_mib(mib: float) -> str:
    """Format a MiB value as a human-readable GiB/TiB string."""
    val = float(mib)
    for unit in ("MiB", "GiB", "TiB", "PiB"):
        if abs(val) < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} EiB"


# --------------------------------------------------------------------------- #
# showalert -n
# --------------------------------------------------------------------------- #
@dataclass
class AlertItem:
    id: int | None
    state: str
    time: str
    severity: str
    type: str
    message: str


_ALERT_KEY_RE = re.compile(
    r"^(Id|State|Message Code|Time|Severity|Type|Message)\s*:\s*(.*)$"
)


def _mk_alert(cur: dict) -> AlertItem:
    return AlertItem(
        id=_to_int(cur.get("Id", "")),
        state=cur.get("State", ""),
        time=cur.get("Time", ""),
        severity=cur.get("Severity", ""),
        type=cur.get("Type", ""),
        message=cur.get("Message", ""),
    )


def parse_showalert(text: str) -> list[AlertItem]:
    """Parse ``showalert -n`` blocks (separated by blank lines)."""
    alerts: list[AlertItem] = []
    cur: dict = {}
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            if cur:
                alerts.append(_mk_alert(cur))
                cur = {}
            continue
        m = _ALERT_KEY_RE.match(s)
        if not m:
            continue  # footer ("13 alerts"), prompt echo, etc.
        key, val = m.group(1), m.group(2).strip()
        if key == "Id" and cur:
            # New block began without a blank separator; flush the previous one.
            alerts.append(_mk_alert(cur))
            cur = {}
        cur[key] = val
    if cur:
        alerts.append(_mk_alert(cur))
    return alerts


# --------------------------------------------------------------------------- #
# showcpg -space
# --------------------------------------------------------------------------- #
@dataclass
class CpgSpace:
    name: str
    used_mib: int
    free_mib: int
    total_mib: int

    @property
    def used_pct(self) -> int:
        return round(self.used_mib / self.total_mib * 100) if self.total_mib else 0


def parse_showcpg_space(text: str) -> list[CpgSpace]:
    """Parse ``showcpg -space``. Rows: Id Name Warn% Used Shared Free Total ...

    The trailing ``total`` row and header/separator lines are skipped.
    """
    out: list[CpgSpace] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        t = s.split()
        if not t[0].isdigit():  # header, dashes, prompt echo
            continue
        if len(t) < 7 or t[1].lower() == "total":
            continue
        total = _to_int(t[6])
        if total is None:
            continue
        out.append(
            CpgSpace(
                name=t[1],
                used_mib=_to_int(t[3]) or 0,
                free_mib=_to_int(t[5]) or 0,
                total_mib=total,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# statcpu -iter 1
# --------------------------------------------------------------------------- #
@dataclass
class CpuStats:
    per_node: dict[int, int] = field(default_factory=dict)  # node -> busy %

    @property
    def overall(self) -> int:
        vals = list(self.per_node.values())
        return round(sum(vals) / len(vals)) if vals else 0


_CPU_TOTAL_RE = re.compile(r"^(\d+),total\s+(\d+)\s+(\d+)\s+(\d+)")


def parse_statcpu(text: str) -> CpuStats:
    """Parse ``statcpu``; busy% per node from the ``N,total`` rows (user+sys)."""
    stats = CpuStats()
    for raw in text.splitlines():
        m = _CPU_TOTAL_RE.match(raw.strip())
        if not m:
            continue
        node = int(m.group(1))
        busy = min(100, int(m.group(2)) + int(m.group(3)))
        stats.per_node[node] = busy
    return stats


# --------------------------------------------------------------------------- #
# statvv -iter 1
# --------------------------------------------------------------------------- #
@dataclass
class VvPerf:
    vv_count: int
    iops: int
    throughput_kbps: int
    latency_ms: float
    busiest_name: str | None = None
    busiest_iops: int = 0


def parse_statvv(text: str) -> VvPerf | None:
    """Parse ``statvv``; return aggregate totals + the busiest volume.

    The total row has 11 tokens (the per-VV ``Max`` columns are blank on it),
    which distinguishes it from data rows (13 tokens) even for a volume literally
    named ``2``.
    """
    total: VvPerf | None = None
    busiest_name: str | None = None
    busiest_iops = -1
    for raw in text.splitlines():
        s = raw.strip()
        if not s or set(s) <= {"-"}:
            continue
        t = s.split()
        if len(t) == 11 and t[0].isdigit() and t[1] == "t":
            total = VvPerf(
                vv_count=int(t[0]),
                iops=_to_int(t[2]) or 0,
                throughput_kbps=_to_int(t[4]) or 0,
                latency_ms=_to_float(t[6]) or 0.0,
            )
        elif len(t) == 13 and t[1] == "t":
            iops_cur = _to_int(t[2]) or 0
            if iops_cur > busiest_iops:
                busiest_iops = iops_cur
                busiest_name = t[0]
    if total is None:
        return None
    if busiest_name is not None:
        total.busiest_name = busiest_name
        total.busiest_iops = max(busiest_iops, 0)
    return total


# --------------------------------------------------------------------------- #
# showrcopy -d groups
# --------------------------------------------------------------------------- #
@dataclass
class RcGroupSummary:
    name: str
    target: str
    status: str
    role: str
    mode: str
    synced: int = 0
    total: int = 0
    last_sync: str = ""

    @property
    def all_synced(self) -> bool:
        return self.total > 0 and self.synced == self.total


def parse_showrcopy_groups(text: str) -> tuple[str, list[RcGroupSummary]]:
    """Parse ``showrcopy -d groups`` -> (system_status, [group summaries])."""
    system_status = ""
    groups: list[RcGroupSummary] = []
    cur: RcGroupSummary | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("status:"):
            system_status = s.split(":", 1)[1].strip()
            continue
        indented = line.startswith(" ")
        t = s.split()
        # Group header data row (not indented): Name ID Target Status Role Mode
        if (
            not indented
            and len(t) >= 5
            and t[1].isdigit()
            and t[3] in ("Started", "Stopped")
        ):
            cur = RcGroupSummary(
                name=t[0],
                target=t[2],
                status=t[3],
                role=t[4],
                mode=t[5] if len(t) > 5 else "",
            )
            groups.append(cur)
            continue
        # Volume row (indented), excluding the "LocalVV ..." column header.
        if indented and cur is not None and not s.lower().startswith(("localvv", "name")):
            if len(t) >= 5:
                cur.total += 1
                if t[4].strip().lower() == "synced":
                    cur.synced += 1
                m = _DATE_RE.search(s)
                if m and m.group(0) > cur.last_sync:
                    cur.last_sync = m.group(0)
    return system_status, groups
