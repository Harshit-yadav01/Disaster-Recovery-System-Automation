"""Tests for the showrcopy parser.

Loads the parser module directly by file path so it can run with a plain
system Python (no venv, no third-party deps, avoids the app.dr package __init__
which imports httpx/paramiko).

Run:
    python tests/test_showrcopy.py      # standalone, prints PASS/FAIL
    pytest tests/test_showrcopy.py      # if pytest is available
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_MODULE_PATH = _HERE.parent / "app" / "dr" / "showrcopy.py"
_FIXTURES = _HERE / "fixtures"

_spec = importlib.util.spec_from_file_location("showrcopy_under_test", _MODULE_PATH)
assert _spec and _spec.loader
showrcopy = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass annotation resolution can find the module.
sys.modules[_spec.name] = showrcopy
_spec.loader.exec_module(showrcopy)


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_primary_side():
    st = showrcopy.parse_showrcopy(_load("showrcopy_primary.txt"))
    assert st.system_status == "Started, Normal"
    assert len(st.targets) == 1
    assert st.targets[0].name == "AlletraMP_E18U31"
    assert st.targets[0].policy == "mirror_config"

    g = st.find_group("Intern_Automation")
    assert g is not None
    assert g.name == "Intern_Automation"          # primary side: no suffix
    assert g.target == "AlletraMP_E18U31"
    assert g.role == "Secondary-Rev"
    assert g.is_started
    assert g.is_secondary
    assert g.is_reversed
    assert len(g.volumes) == 4
    assert g.all_synced()
    assert g.volumes[0].local_vv == "Automation1"
    assert g.volumes[0].local_id == 6199
    assert g.volumes[0].remote_id == 687
    assert g.volumes[0].sync_status == "Synced"


def test_secondary_side():
    st = showrcopy.parse_showrcopy(_load("showrcopy_secondary.txt"))
    assert st.system_status == "Started, Normal"

    g = st.find_group("Intern_Automation")
    assert g is not None
    assert g.name == "Intern_Automation.r188150"  # DR side: .r<sysID> suffix
    assert g.role == "Primary-Rev"
    assert g.is_primary
    assert g.is_reversed
    assert g.is_started
    assert len(g.volumes) == 4
    assert g.all_synced()
    # On the DR side the local/remote IDs are the mirror of the primary side.
    assert g.volumes[0].local_id == 687
    assert g.volumes[0].remote_id == 6199


def test_find_group_missing_returns_none():
    st = showrcopy.parse_showrcopy(_load("showrcopy_primary.txt"))
    assert st.find_group("Nonexistent_Group") is None


def _run_standalone() -> int:
    tests = [
        test_primary_side,
        test_secondary_side,
        test_find_group_missing_returns_none,
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
