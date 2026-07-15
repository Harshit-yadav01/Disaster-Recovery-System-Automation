"""Parsers for HPE Alletra / 3PAR ``showvlun`` and ``showhost`` CLI output.

Read-only. Turns raw CLI text into structured objects so the present-to-host
workflow can:
  * list the DR array's hosts (to pick a presentation target),
  * see which volumes are currently exported and on which LUN,
  * read the PRIMARY array's LUN assignments so DR exports can match them.

Pure standard library (no third-party deps) so it can be unit-tested without a
venv or network, exactly like ``showrcopy.py``. The DR SSH engine feeds it the
text captured from an array.

NOTE: 3PAR/Alletra CLI column layouts vary slightly by model/firmware. These
parsers are deliberately tolerant (positional-from-both-ends, skip separators)
and should be validated against real output via ``dr_presentation.py`` before
the write workflows are trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Vlun",
    "Host",
    "parse_showvlun",
    "parse_showhost",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Vlun:
    """One VLUN row from ``showvlun`` (an active export or a template)."""

    lun: int | None
    vv_name: str
    host_name: str
    port: str
    type: str            # host | hostset | port | matched (best-effort)
    host_wwn: str = ""
    template: bool = False  # True if from the lower "VLUN Templates" section


@dataclass
class Host:
    """One host from ``showhost`` (with its WWN/iSCSI paths collapsed)."""

    id: int | None
    name: str
    persona: str
    wwns: list[str] = field(default_factory=list)

    @property
    def is_vmware(self) -> bool:
        return "vmware" in (self.persona or "").strip().lower()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_separator(line: str) -> bool:
    s = line.strip()
    return not s or set(s) <= set("-= ") or s.lower().endswith("total")


def _looks_like_total(line: str) -> bool:
    return "total" in line.strip().lower()


# --------------------------------------------------------------------------- #
# showvlun
# --------------------------------------------------------------------------- #
def parse_showvlun(text: str) -> list[Vlun]:
    """Parse ``showvlun`` (default columns: Lun VVName HostName Host_WWN Port Type).

    Handles the two sections (active VLUNs + VLUN templates); rows in the lower
    section are flagged ``template=True``. Rows whose LUN is not an integer are
    skipped. Tolerant of ``---`` placeholders and extra whitespace.
    """
    vluns: list[Vlun] = []
    template_section = False
    in_table = False

    for raw in text.splitlines():
        line = raw.rstrip()
        low = line.strip().lower()

        # Detect the templates section header text if present.
        if "template" in low and "vlun" in low:
            template_section = True
            in_table = False
            continue

        # Header row of a table: contains "lun" and "vvname".
        if ("lun" in low and "vvname" in low):
            in_table = True
            continue

        if not in_table:
            continue

        if _is_separator(line):
            # A blank/dashes/total line ends the current table body.
            if _looks_like_total(line):
                in_table = False
            continue

        tokens = line.split()
        if len(tokens) < 3:
            continue

        # LUN is the first column and must be an integer.
        try:
            lun: int | None = int(tokens[0])
        except ValueError:
            continue

        vv_name = tokens[1]
        host_name = tokens[2]
        vtype = tokens[-1]
        port = tokens[-2] if len(tokens) >= 5 else ""
        # WWN sits between host and port when present (active VLUNs); templates
        # often show "---" for WWN. Best-effort: token index 3 if it isn't the
        # port/type we've already consumed.
        host_wwn = tokens[3] if len(tokens) >= 6 else ""

        vluns.append(Vlun(
            lun=lun,
            vv_name=vv_name,
            host_name=host_name,
            port=port,
            type=vtype,
            host_wwn=host_wwn,
            template=template_section,
        ))

    return vluns


# --------------------------------------------------------------------------- #
# showhost
# --------------------------------------------------------------------------- #
def parse_showhost(text: str) -> list[Host]:
    """Parse ``showhost`` (default columns: Id Name Persona -WWN/iSCSI_Name- Port).

    A new host row begins with an integer Id; continuation lines (blank Id) add
    more WWN/iSCSI paths to the current host. Tolerant of layout variations.
    """
    hosts: list[Host] = []
    in_table = False
    current: Host | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        low = line.strip().lower()

        if low.startswith("id") and "name" in low:
            in_table = True
            continue
        if not in_table:
            continue
        if _is_separator(line):
            if _looks_like_total(line):
                in_table = False
                current = None
            continue

        tokens = line.split()
        if not tokens:
            continue

        # New host: first token is an integer Id.
        try:
            hid = int(tokens[0])
            is_new = True
        except ValueError:
            is_new = False
            hid = None

        if is_new:
            name = tokens[1] if len(tokens) > 1 else ""
            persona = tokens[2] if len(tokens) > 2 else ""
            wwn = tokens[3] if len(tokens) > 3 else ""
            current = Host(id=hid, name=name, persona=persona,
                           wwns=[wwn] if wwn and wwn != "--" else [])
            hosts.append(current)
        elif current is not None:
            # Continuation line: an additional WWN/iSCSI path for the same host.
            wwn = tokens[0]
            if wwn and wwn not in ("--", "---"):
                current.wwns.append(wwn)

    return hosts
