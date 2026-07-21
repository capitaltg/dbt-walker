"""Shared fixture paths and skip helpers.

The large/real fixtures are gitignored and generated on demand. Tests that need
them are skipped (with a message naming the command to build them) when absent,
so a fresh clone still runs the fast suite.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))  # let tests import gen_fixture

GENERATED = REPO_ROOT / "tests" / "fixtures" / "generated"
SYNTH = GENERATED / "synth"
SYNTH_SMALL = GENERATED / "synth_small"
COUNTMONEY = REPO_ROOT / "tests" / "fixtures" / "real" / "CountMoney" / "CountMoney_model"


def _manifest(project: Path) -> Path:
    return project / "target" / "manifest.json"


def needs(project: Path, build_cmd: str):
    """A skipif mark: skip unless <project>/target/manifest.json exists."""
    return pytest.mark.skipif(
        not _manifest(project).exists(),
        reason=f"fixture missing at {project} — build it with:\n    {build_cmd}",
    )


def ground_truth(project: Path) -> dict:
    return json.loads((project / "ground_truth.json").read_text(encoding="utf-8"))


GEN_SMALL_CMD = (
    ".venv\\Scripts\\python scripts/gen_fixture.py "
    "--out tests/fixtures/generated/synth_small --models 60 --seed 42 --dbt build"
)
GEN_BIG_CMD = (
    ".venv\\Scripts\\python scripts/gen_fixture.py "
    "--out tests/fixtures/generated/synth --models 2000 --seed 42 --dbt compile"
)
FETCH_CMD = ".venv\\Scripts\\python scripts/fetch_real_fixture.py"
MOCK_WH_CMD = ".venv\\Scripts\\python scripts/mock_countmoney_warehouse.py"


def needs_catalog(project: Path):
    """Skip unless <project>/target/catalog.json exists (dbt docs generate)."""
    return pytest.mark.skipif(
        not (project / "target" / "catalog.json").exists(),
        reason=f"catalog missing at {project} — build it with:\n    {MOCK_WH_CMD}",
    )
