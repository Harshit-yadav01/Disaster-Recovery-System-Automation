"""Parser for HPE Alletra / 3PAR ``showrcopy`` CLI output.

Turns the raw text of a ``showrcopy`` command into structured objects so the DR
workflows can make decisions on real state (roles + per-volume SyncStatus)
instead of scraping text ad-hoc.

Pure standard-library (no third-party deps) so it can be unit-tested without a
venv or network. The DR SSH engine feeds it the output captured from an array.

Handles both arrays' formats:
  * Primary side group name  : ``Intern_Automation``
  * DR side group name       : ``Intern_Automation.r188150``  (.r<primarySysID>)
  * Roles                    : Primary | Secondary | Primary-Rev | Secondary-Rev
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "RcopyTarget",
    "RcopyLink",
    "RcopyVolume",
    "RcopyGroup",
    "RcopyStatus",
    "parse_showrcopy",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class RcopyTarget:
    name: str
    id: int | None
    type: str
    status: str
    options: str
    policy: str


@dataclass
class RcopyLink:
    target: str
    node: str
    address: str
    status: str
    options: str


@dataclass
class RcopyVolume:
    local_vv: str
    local_id: int | None
    remote_vv: str
    remote_id: int | None
    sync_status: str
    last_sync_time: str

    @property
    def is_synced(self) -> bool:
        return self.sync_status.strip().lower() == "synced"


@dataclass
class RcopyGroup:
    name: str
    target: str
    status: str
    role: str
    mode: str
    options: str
    volumes: list[RcopyVolume] = field(default_factory=list)

    @property
    def is_started(self) -> bool:
        return self.status.strip().lower() == "started"

    @property
    def is_stopped(self) -> bool:
        return self.status.strip().lower() == "stopped"

    @property
    def is_primary(self) -> bool:
        """True for Primary or Primary-Rev roles."""
        return self.role.strip().lower().startswith("primary")

    @property
    def is_secondary(self) -> bool:
        """True for Secondary or Secondary-Rev roles."""
        return self.role.strip().lower().startswith("secondary")

    @property
    def is_reversed(self) -> bool:
        """True for the temporary -Rev roles (post-failover state)."""
        return self.role.strip().lower().endswith("-rev")

    def all_synced(self) -> bool:
        """True only if there is at least one volume and every one is Synced."""
        return bool(self.volumes) and all(v.is_synced for v in self.volumes)


@dataclass
class RcopyStatus:
    system_status: str
    targets: list[RcopyTarget] = field(default_factory=list)
    links: list[RcopyLink] = field(default_factory=list)
    groups: list[RcopyGroup] = field(default_factory=list)

    def find_group(self, base_name: str) -> RcopyGroup | None:
        """Return the group matching ``base_name`` on this array.

        Matches either the exact name (primary side, e.g. ``Intern_Automation``)
        or the ``.r<sysID>`` suffixed name (DR side, e.g.
        ``Intern_Automation.r188150``).
        """
        for g in self.groups:
            if g.name == base_name or g.name.startswith(base_name + "."):
                return g
        return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _to_int(token: str) -> int | None:
    try:
        return int(token)
    except (ValueError, TypeError):
        return None


def _is_header(stripped: str, section: str | None) -> bool:
    """Detect a column-header row so it can be skipped."""
    low = stripped.lower()
    if section == "target":
        return low.startswith("name") and "type" in low and "policy" in low
    if section == "link":
        return low.startswith("target") and "address" in low
    if section == "group":
        group_hdr = low.startswith("name") and "role" in low and "mode" in low
        vol_hdr = low.startswith("localvv") and "syncstatus" in low
        return group_hdr or vol_hdr
    return False


def _parse_target_row(stripped: str) -> RcopyTarget | None:
    t = stripped.split()
    if len(t) < 4:
        return None
    return RcopyTarget(
        name=t[0],
        id=_to_int(t[1]),
        type=t[2] if len(t) > 2 else "",
        status=t[3] if len(t) > 3 else "",
        options=t[4] if len(t) > 4 else "",
        policy=" ".join(t[5:]) if len(t) > 5 else "",
    )


def _parse_link_row(stripped: str) -> RcopyLink | None:
    t = stripped.split()
    if len(t) < 4:
        return None
    return RcopyLink(
        target=t[0],
        node=t[1] if len(t) > 1 else "",
        address=t[2] if len(t) > 2 else "",
        status=t[3] if len(t) > 3 else "",
        options=t[4] if len(t) > 4 else "",
    )


def _parse_group_row(stripped: str) -> RcopyGroup | None:
    t = stripped.split()
    if len(t) < 5:
        return None
    return RcopyGroup(
        name=t[0],
        target=t[1],
        status=t[2],
        role=t[3],
        mode=t[4],
        options=" ".join(t[5:]) if len(t) > 5 else "",
    )


def _parse_vol_row(stripped: str) -> RcopyVolume | None:
    t = stripped.split()
    if len(t) < 5:
        return None
    return RcopyVolume(
        local_vv=t[0],
        local_id=_to_int(t[1]),
        remote_vv=t[2],
        remote_id=_to_int(t[3]),
        sync_status=t[4],
        last_sync_time=" ".join(t[5:]) if len(t) > 5 else "",
    )


def parse_showrcopy(text: str) -> RcopyStatus:
    """Parse raw ``showrcopy`` output into an :class:`RcopyStatus`.

    Tolerant of a leading prompt/command-echo line and of the tab/space mix seen
    in real output. Unknown or malformed rows are skipped rather than raising.
    """
    system_status = ""
    targets: list[RcopyTarget] = []
    links: list[RcopyLink] = []
    groups: list[RcopyGroup] = []

    section: str | None = None
    current_group: RcopyGroup | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # Section markers
        if stripped == "Remote Copy System Information":
            section = None
            continue
        if stripped == "Target Information":
            section = "target"
            continue
        if stripped == "Link Information":
            section = "link"
            continue
        if stripped == "Group Information":
            section = "group"
            continue

        # System status line (appears before any section)
        if section is None and stripped.startswith("Status:"):
            system_status = stripped.split(":", 1)[1].strip()
            continue

        # Skip column headers
        if _is_header(stripped, section):
            continue

        if section == "target":
            row = _parse_target_row(stripped)
            if row:
                targets.append(row)
        elif section == "link":
            row = _parse_link_row(stripped)
            if row:
                links.append(row)
        elif section == "group":
            indented = bool(raw[:1]) and raw[:1].isspace()
            if indented:
                vol = _parse_vol_row(stripped)
                if vol and current_group is not None:
                    current_group.volumes.append(vol)
            else:
                grp = _parse_group_row(stripped)
                if grp:
                    groups.append(grp)
                    current_group = grp

    return RcopyStatus(
        system_status=system_status,
        targets=targets,
        links=links,
        groups=groups,
    )
