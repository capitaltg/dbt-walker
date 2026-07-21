"""Fetch a real public dbt project as a test fixture for dbt-walker.

Default (and only) target: flyanakin/CountMoney — a small real-world Postgres
project (matching dbt-walker's primary dialect). We can't connect to its
warehouse, but we don't need to:

  * `dbt parse` runs fully offline with dummy credentials and produces a real
    manifest.json (model-/robustness-level coverage).
  * `dbt compile` against an embedded DuckDB profile renders the real
    Postgres-dialect SQL into target/compiled/ (a bonus for future column-level
    lineage tests). Compile only renders jinja + resolves refs; it does not
    execute the models or their post-hooks, so Postgres-specific SQL is fine.

Idempotent: skips the clone/parse if outputs already exist; `--force` refetches.

Usage:
    python scripts/fetch_real_fixture.py [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/flyanakin/CountMoney"
# repo-relative location of the dbt project and its profile
PROJECT_SUBDIR = "CountMoney_model"
PROFILE_NAME = "CountMoney_model"
# profile references these env_var()s with no defaults -> dummies satisfy parse
DUMMY_ENV = {
    "WAREHOUSE_HOST": "localhost",
    "WAREHOUSE_USER": "dummy",
    "WAREHOUSE_SECRET": "dummy",
    # CountMoney contains UTF-8 (Chinese) source files; force Python UTF-8 mode so
    # dbt doesn't fall back to the Windows cp1252 locale and crash reading them.
    "PYTHONUTF8": "1",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "tests" / "fixtures" / "real" / "CountMoney"


def _dbt_exe() -> str:
    for cand in (Path(sys.prefix) / "Scripts" / "dbt.exe", Path(sys.prefix) / "bin" / "dbt"):
        if cand.exists():
            return str(cand)
    found = shutil.which("dbt")
    if not found:
        sys.exit("error: could not find the dbt executable (looked in the venv and PATH).")
    return found


def _ensure_adapter(module: str, package: str) -> None:
    try:
        __import__(module)
        return
    except ImportError:
        pass
    print(f"Installing {package} ...", flush=True)
    proc = subprocess.run([sys.executable, "-m", "pip", "install", package])
    if proc.returncode != 0:
        sys.exit(f"error: failed to install {package}.")


def _clone(force: bool) -> None:
    if DEST.exists():
        if not force:
            print(f"Clone exists at {DEST} (use --force to refetch).")
            return
        shutil.rmtree(DEST)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Cloning {REPO_URL} ...", flush=True)
    proc = subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(DEST)])
    if proc.returncode != 0:
        sys.exit("error: git clone failed.")


def _run_dbt(command: str, project_dir: Path, profiles_dir: Path, env: dict) -> bool:
    full_env = {**os.environ, **env}
    proc = subprocess.run(
        [_dbt_exe(), command, "--project-dir", str(project_dir),
         "--profiles-dir", str(profiles_dir)],
        env=full_env,
    )
    return proc.returncode == 0


def _summarize(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes", {})
    kinds: dict[str, int] = {}
    for node in nodes.values():
        kinds[node.get("resource_type", "?")] = kinds.get(node.get("resource_type", "?"), 0) + 1
    kinds["source"] = len(manifest.get("sources", {}))
    print(f"\nManifest: {manifest_path}")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Fetch the CountMoney dbt project as a fixture.")
    p.add_argument("--force", action="store_true", help="re-clone even if it exists")
    args = p.parse_args(argv)

    _clone(args.force)
    project_dir = DEST / PROJECT_SUBDIR
    if not project_dir.exists():
        sys.exit(f"error: expected dbt project at {project_dir} — repo layout changed?")

    _ensure_adapter("dbt.adapters.postgres", "dbt-postgres")

    # 1) real Postgres manifest via offline parse (dummy creds)
    print("\n=== dbt parse (postgres, offline, dummy creds) ===", flush=True)
    profiles_dir = project_dir / "config"
    if not _run_dbt("parse", project_dir, profiles_dir, DUMMY_ENV):
        sys.exit("error: `dbt parse` failed.")
    manifest = project_dir / "target" / "manifest.json"
    if not manifest.exists():
        sys.exit(f"error: parse succeeded but no manifest at {manifest}.")
    _summarize(manifest)

    # 2) bonus: compiled Postgres SQL via an embedded DuckDB profile (non-gating)
    print("\n=== dbt compile (duckdb, bonus - real compiled SQL) ===", flush=True)
    duck_profiles = DEST / "_duckdb_profiles"
    duck_profiles.mkdir(exist_ok=True)
    duck_path = (DEST / "countmoney.duckdb").resolve().as_posix()
    (duck_profiles / "profiles.yml").write_text(
        f"{PROFILE_NAME}:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        f"      path: {duck_path}\n",
        encoding="utf-8",
    )
    if _run_dbt("compile", project_dir, duck_profiles, DUMMY_ENV):
        compiled = list((project_dir / "target" / "compiled").rglob("*.sql"))
        print(f"  compiled {len(compiled)} SQL files (available for phase-2 column tests).")
    else:
        print("  NOTE: duckdb compile did not fully succeed — manifest (parse) is still "
              "usable. Compiled SQL is a nice-to-have, not required this iteration.")

    print(f"\nDone. Fixture at {project_dir}")


if __name__ == "__main__":
    main()
