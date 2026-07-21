"""Tests for `build-app`: payload extraction, staleness, and the generated file."""
import json
import re
from pathlib import Path

import pytest

from conftest import GEN_SMALL_CMD, SYNTH_SMALL, needs
from dbt_walker.graph import Graph

pytest.importorskip("sqlglot", reason="app column data needs the [col] extra")


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_payload_shape():
    from dbt_walker import app

    graph = Graph.load(SYNTH_SMALL)
    p = app.collect(graph, Path(SYNTH_SMALL))

    for key in ("nodes", "parents", "children", "tests", "columns", "sql", "project"):
        assert key in p, f"payload missing {key}"
    # edges only ever point at nodes we actually carry (the app can't render dangling ids)
    carried = set(p["nodes"])
    for uid, parents in p["parents"].items():
        assert uid in carried
        assert all(x in carried for x in parents)
    # tests are summarized per-model, not carried as nodes
    assert not any(meta["type"] == "test" for meta in p["nodes"].values())
    # column edge graph present for models, with transform kinds
    model_ids = [u for u, m in p["nodes"].items() if m["type"] == "model"]
    assert set(p["columns"]) == set(model_ids)
    sample = next(c for c in p["columns"].values() if c["resolved"] and c["cols"])
    edges = next(iter(sample["cols"].values()))
    if edges:
        assert len(edges[0]) == 3  # [parent, column, transform]


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_project_metadata_and_counts():
    from dbt_walker import app

    graph = Graph.load(SYNTH_SMALL)
    project = app.collect(graph, Path(SYNTH_SMALL))["project"]
    assert project["name"]
    assert project["adapter"] == "duckdb"
    assert sum(project["counts"].values()) > 0
    assert "staleness" in project


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_default_filename_has_project_and_timestamp():
    from dbt_walker import app

    graph = Graph.load(SYNTH_SMALL)
    payload = app.collect(graph, Path(SYNTH_SMALL))
    name = app.default_filename(payload)
    assert name.endswith(".html")
    assert payload["project"]["name"] in name
    assert re.search(r"\d{8}-\d{4}\.html$", name), name


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_build_app_writes_self_contained_file(tmp_path):
    from dbt_walker import cli

    out = tmp_path / "app"
    cli.main(["--project-dir", str(SYNTH_SMALL), "build-app", "--out", str(out) + "/"])
    files = list(out.glob("*.html"))
    assert len(files) == 1
    html = files[0].read_text(encoding="utf-8")

    # offline: mermaid is inlined, nothing is fetched
    assert "globalThis.mermaid" in html
    assert "cdn.jsdelivr.net" not in html
    assert "<script src=" not in html
    # the three panes and the controls are present
    for marker in ('class="pane tree"', 'id="graph"', 'class="pane detail"',
                   'id="modeSeg"', 'id="colChips"', 'id="treeSearch"'):
        assert marker in html, marker
    # empty states teach what populates each pane (design Q1)
    assert "Select a model" in html and "Click a node" in html
    # payload embedded and parseable
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1)
                      .replace("<\\/", "</"))
    assert data["nodes"] and data["project"]["name"]


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_project_root_accepted_positionally_and_via_flag(tmp_path):
    """`build-app <root>` is the documented form; --project-dir must also work."""
    from dbt_walker import cli

    positional = tmp_path / "a.html"
    cli.main(["build-app", str(SYNTH_SMALL), "--out", str(positional)])
    assert positional.exists()

    via_flag = tmp_path / "b.html"
    cli.main(["--project-dir", str(SYNTH_SMALL), "build-app", "--out", str(via_flag)])
    assert via_flag.exists()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_nested_folders_are_captured_as_paths(tmp_path):
    """models/marts/finance/reporting/x.sql must yield the full nested path so the
    tree can render it hierarchically (not one flat label)."""
    import json as _json
    import shutil

    from dbt_walker import app

    proj = tmp_path / "proj"
    shutil.copytree(SYNTH_SMALL, proj)
    manifest_path = proj / "target" / "manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    # relocate one model into a deep subfolder
    target = next(n for n in manifest["nodes"].values()
                  if n.get("resource_type") == "model" and n.get("original_file_path"))
    target["original_file_path"] = "models/marts/finance/reporting/deep.sql"
    manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")

    payload = app.collect(Graph.load(proj), proj, include_sql=False)
    folders = {m["folder"] for m in payload["nodes"].values()}
    assert "marts/finance/reporting" in folders, folders


def test_script_close_is_escaped_in_payload():
    from dbt_walker import app_template

    embedded = app_template._embed({"sql": {"m": {"raw": "select '</script>' as x"}}})
    assert "</script>" not in embedded


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_staleness_flags_models_newer_than_manifest(tmp_path):
    """A model edited after the last compile must be reported, so nobody trusts
    a stale picture (design Q4)."""
    import shutil

    from dbt_walker import app

    proj = tmp_path / "proj"
    shutil.copytree(SYNTH_SMALL, proj)
    graph = Graph.load(proj)

    assert app.staleness(graph, proj)["stale"] is False
    # touch a model so it's newer than the manifest
    model = next(proj.glob("models/**/*.sql"))
    model.touch()
    stale = app.staleness(graph, proj)
    assert stale["stale"] is True and stale["newer_count"] >= 1


def test_build_app_without_manifest_explains_how_to_fix(tmp_path, capsys):
    from dbt_walker import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--project-dir", str(tmp_path), "build-app"])
    assert "dbt compile" in str(exc.value)
