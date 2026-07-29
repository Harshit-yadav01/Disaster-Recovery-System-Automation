"""Tests for the stats parsers (showalert / showcpg -space / statcpu / statvv /
showrcopy -d groups).

Loads the parser module directly by file path so it can run with a plain system
Python (no venv, no third-party deps, avoids the app.dr package __init__ which
imports httpx/paramiko).

Run:
    python tests/test_stats.py      # standalone, prints PASS/FAIL
    pytest tests/test_stats.py      # if pytest is available
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "app" / "dr" / "stats.py"
_FIXTURES = _HERE / "fixtures"

_spec = importlib.util.spec_from_file_location("stats_under_test", _MODULE_PATH)
assert _spec and _spec.loader
stats = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stats
_spec.loader.exec_module(stats)


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_showalert():
    alerts = stats.parse_showalert(_load("showalert.txt"))
    assert len(alerts) == 6
    first = alerts[0]
    assert first.id == 125
    assert first.severity == "Fatal"
    assert first.type == "Notification"
    assert first.message == "HPE added test ABTS event 1"
    # Message value containing colons is preserved whole.
    quorum = next(a for a in alerts if a.id == 47)
    assert quorum.message == "Node: 1 SysId: 188150 Quorum server unreachable."
    assert stats.severity_tone("Fatal") == "red"
    assert stats.severity_tone("Major") == "red"
    assert stats.severity_tone("Minor") == "warning"
    assert stats.severity_tone("Degraded") == "warning"


def test_showcpg_space():
    cpgs = stats.parse_showcpg_space(_load("showcpg_space.txt"))
    assert len(cpgs) == 3  # total row excluded
    names = {c.name for c in cpgs}
    assert names == {"3sc", "SSD_r6", "test"}
    ssd = next(c for c in cpgs if c.name == "SSD_r6")
    assert ssd.used_mib == 1037925
    assert ssd.free_mib == 419475
    assert ssd.total_mib == 1798650
    assert ssd.used_pct == 58


def test_statcpu():
    cpu = stats.parse_statcpu(_load("statcpu.txt"))
    assert cpu.per_node == {0: 3, 1: 4}
    assert cpu.overall == 4  # round((3+4)/2)


def test_statvv():
    perf = stats.parse_statvv(_load("statvv.txt"))
    assert perf is not None
    assert perf.vv_count == 189
    assert perf.iops == 133
    assert perf.throughput_kbps == 612
    assert perf.latency_ms == 0.143
    # Busiest real volume is 700gb (124 IOPS), not the total row or the vol "2".
    assert perf.busiest_name == "700gb"
    assert perf.busiest_iops == 124


def test_showrcopy_groups():
    system_status, groups = stats.parse_showrcopy_groups(_load("showrcopy_d_groups.txt"))
    assert system_status == "Started, Normal"
    assert len(groups) == 4
    by_name = {g.name: g for g in groups}

    app = by_name["APP_Test"]
    assert app.status == "Started"
    assert app.role == "Secondary"
    assert app.mode == "Sync"
    assert (app.synced, app.total) == (1, 1)
    assert app.all_synced

    intern = by_name["Intern_Automation"]
    assert intern.role == "Primary"
    assert (intern.synced, intern.total) == (4, 4)

    stopped = by_name["Test-RCG"]
    assert stopped.status == "Stopped"
    assert (stopped.synced, stopped.total) == (0, 2)
    assert stopped.last_sync == "2026-07-27 18:50:42"


def _run_standalone() -> int:
    tests = [
        test_showalert,
        test_showcpg_space,
        test_statcpu,
        test_statvv,
        test_showrcopy_groups,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
