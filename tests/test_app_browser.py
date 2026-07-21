"""End-to-end browser tests for the generated lineage app (Playwright).

Everything else about the app is checked statically — this is the only place the
JS actually *runs*: mermaid renders, the panes populate, clicking a node opens
its SQL. Any console error fails the test, so a broken template can't ship.

Skipped when playwright (or its chromium) isn't installed:
    pip install playwright && python -m playwright install chromium
"""
import pytest

from conftest import (COUNTMONEY, FETCH_CMD, GEN_SMALL_CMD, SYNTH_SMALL, needs,
                      needs_catalog)

pytest.importorskip("sqlglot", reason="app column data needs the [col] extra")
sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


def pick_col(pg, name, keep_open=False):
    """Add a column via the two-pane picker: open it, click the column in the
    Available pane, then close (so the popover doesn't overlay later assertions)."""
    if pg.locator("#colPop").is_hidden():
        pg.locator("#colBtn").click()
        pg.wait_for_selector("#colPop:not([hidden])")
    pg.locator(f'#colAvail .colpop-item[data-col="{name}"]').first.click()
    pg.wait_for_timeout(120)
    if not keep_open:
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(60)


@pytest.fixture(scope="module")
def app_file(tmp_path_factory):
    """Generate the app once for the whole module."""
    from dbt_walker import cli

    if not (SYNTH_SMALL / "target" / "manifest.json").exists():
        pytest.skip(f"fixture missing — build it with:\n    {GEN_SMALL_CMD}")
    out = tmp_path_factory.mktemp("app") / "app.html"
    cli.main(["build-app", str(SYNTH_SMALL), "--out", str(out)])
    return out


@pytest.fixture
def page(app_file):
    """A loaded page, with console errors collected and asserted at teardown."""
    errors = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(app_file.as_uri())
        pg.wait_for_load_state("networkidle")
        yield pg
        browser.close()
    assert not errors, f"browser console errors: {errors}"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_app_loads_with_empty_states(page):
    """Before choosing anything, each pane should say what will fill it (design Q1)."""
    assert "lineage explorer" in page.title()
    assert page.locator("#treeBody .folder").count() > 0, "model tree should list folders"
    assert "Select a model" in page.locator("#graph").inner_text()
    assert "Pick a model" in page.locator("#results").inner_text()
    assert "Click a node" in page.locator("#sqlPanel").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_selecting_a_model_renders_the_graph(page):
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    assert page.locator("#graph svg .node").count() > 1, "graph should draw the lineage"
    assert "mart_0" in page.locator("#results").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_clicking_a_node_opens_its_sql(page):
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    # click a MODEL node (sources/seeds legitimately have no SQL to show)
    page.locator("#graph svg .node", has_text="mart_0").first.click()
    page.wait_for_selector("#sqlPanel .sqlbox")
    assert "select" in page.locator("#sqlPanel .sqlbox").inner_text().lower()
    # the clicked node is highlighted in the graph
    assert page.locator("#graph svg .node.selected").count() == 1
    # and the compiled/raw toggle switches content
    page.locator('#sqlPanel [data-sql="raw"]').click()
    assert page.locator('#sqlPanel [data-sql="raw"][aria-pressed="true"]').count() == 1


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_sql_has_a_line_number_gutter(page):
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    page.locator("#graph svg .node", has_text="mart_0").first.click()
    page.wait_for_selector("#sqlPanel .sqlbox")

    nums = page.locator("#sqlPanel .sqlbox i.ln")
    lines = page.locator("#sqlPanel .sqlbox > span")
    assert nums.count() == lines.count() > 1, "every SQL line gets a number"
    assert [nums.nth(i).inner_text() for i in range(3)] == ["1", "2", "3"]
    # the gutter must not end up in copied SQL
    assert page.locator("#sqlPanel .sqlbox i.ln").first.evaluate(
        "e => getComputedStyle(e).userSelect") == "none"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_impact_mode_shows_refresh_plan_and_prerequisites(page):
    """int_38 has incremental ancestors, so it must show the upstream refresh order."""
    page.select_option("#modelPick", label="int_38")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")
    # section labels are uppercased by CSS, so compare case-insensitively
    text = page.locator("#results").inner_text().lower()
    assert "refresh these first" in text, "upstream prerequisites section missing"
    assert "int_11" in text and "int_24" in text, "prerequisites should be listed in order"
    assert "needs full refresh" in text and "rebuild normally" in text
    assert "dbt build --select" in text


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_column_chips_narrow_the_impact(page):
    """Adding a column chip should prune the affected set below the whole-model count."""
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")
    whole_model = page.locator("#graph svg .node").count()

    pick_col(page, "val1")
    page.wait_for_function(
        "n => document.querySelectorAll('#graph svg .node').length !== n", arg=whole_model
    )
    assert page.locator("#colChips .chip").count() == 1
    assert page.locator("#graph svg .node").count() < whole_model
    assert "read" in page.locator("#results").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_panes_collapse_and_resize(page):
    body = page.locator("#body")
    page.locator("#hideTree").click()
    assert "no-tree" in (body.get_attribute("class") or "")
    page.locator("#showTree").click()
    assert "no-tree" not in (body.get_attribute("class") or "")

    # drag the detail splitter left; the pane should get wider
    before = page.locator(".pane.detail").bounding_box()["width"]
    sp = page.locator("#splitDetail").bounding_box()
    page.mouse.move(sp["x"] + sp["width"] / 2, sp["y"] + 200)
    page.mouse.down()
    page.mouse.move(sp["x"] - 150, sp["y"] + 200)
    page.mouse.up()
    assert page.locator(".pane.detail").bounding_box()["width"] > before + 50


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_node_labels_have_no_escaping_artifacts(page):
    """mermaid.render() double-escapes HTML entities, which once leaked a stray
    '&' into every node label ('&◆ CHANGED HERE'). Labels must read cleanly."""
    page.select_option("#modelPick", label="int_38")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")
    labels = page.locator("#graph svg .node").all_text_contents()
    assert labels, "expected rendered nodes"
    joined = " | ".join(labels)
    assert "&" not in joined, f"escaping artifact in node labels: {joined[:200]}"
    # the target model is marked by its TARGET badge + crimson outline, not a banner
    assert any("int_38" in lbl for lbl in labels)
    assert page.locator("#graph .rb-target").count() == 1, "target node should carry a TARGET badge"
    assert "TARGET" in page.locator("#graphNote").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_mode_round_trip_restores_the_view(page):
    """Regression: switching to Impact forced direction=downstream and switching
    back to Lineage never restored it, so the original both-directions view was
    unreachable. Model -> Impact -> Lineage must return to where it started."""
    page.select_option("#modelPick", label="int_38")
    page.wait_for_selector("#graph svg .node")
    lineage_nodes = page.locator("#graph svg .node").count()
    assert page.locator("#dirPick").input_value() == "both"

    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_function(
        "n => document.querySelectorAll('#graph svg .node').length !== n", arg=lineage_nodes
    )
    assert page.locator("#dirPick").input_value() == "down", "impact looks downstream"

    page.locator('#modeSeg button[data-mode="lineage"]').click()
    page.wait_for_function(
        "n => document.querySelectorAll('#graph svg .node').length === n", arg=lineage_nodes
    )
    assert page.locator("#dirPick").input_value() == "both", "lineage must restore both directions"
    assert page.locator("#graph svg .node").count() == lineage_nodes


# --------------------------------------------------------------- multi-step flows
# Each of these drives a SEQUENCE of interactions. Single-action tests start from
# a fresh load and so miss state that leaks between steps — which is exactly how
# the mode/direction bug survived.

@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_columns_persist_across_mode_switches(page):
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    pick_col(page, "val1")
    page.wait_for_selector("#colChips .chip")

    for mode in ("columns", "lineage", "impact"):
        page.locator(f'#modeSeg button[data-mode="{mode}"]').click()
        page.wait_for_selector("#graph svg .node")
        assert page.locator("#colChips .chip").count() == 1, f"chip lost switching to {mode}"
        assert page.locator(f'#modeSeg button[data-mode="{mode}"][aria-pressed="true"]').count() == 1


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_switching_model_resets_columns_and_redraws(page):
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    pick_col(page, "val1")
    page.wait_for_selector("#colChips .chip")

    page.select_option("#modelPick", label="int_38")   # a different model
    page.wait_for_selector("#graph svg .node")
    assert page.locator("#colChips .chip").count() == 0, "columns must reset for a new model"
    assert "int_38" in page.locator("#results").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_adding_then_removing_a_column_restores_the_wider_radius(page):
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")
    whole = page.locator("#graph svg .node").count()

    pick_col(page, "val1")
    page.wait_for_function("n => document.querySelectorAll('#graph svg .node').length !== n",
                           arg=whole)
    narrowed = page.locator("#graph svg .node").count()
    assert narrowed < whole

    page.locator("#colChips .chip button").click()          # remove the chip
    page.wait_for_function("n => document.querySelectorAll('#graph svg .node').length === n",
                           arg=whole)
    assert page.locator("#colChips .chip").count() == 0


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_additive_toggle_moves_models_between_lists(page):
    """Toggling additive must re-plan: safe incrementals slide out of full-refresh."""
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")

    def counts():
        t = page.locator("#results").inner_text()
        full = t.split("NEEDS FULL REFRESH (")[1].split(")")[0]
        rebuild = t.split("REBUILD NORMALLY (")[1].split(")")[0]
        return int(full), int(rebuild)

    full_before, rebuild_before = counts()
    page.locator("#additive").check()
    page.wait_for_timeout(250)
    full_after, rebuild_after = counts()
    assert full_after <= full_before, "additive can only shrink the full-refresh list"
    assert full_after + rebuild_after == full_before + rebuild_before, "models must not vanish"

    page.locator("#additive").uncheck()
    page.wait_for_timeout(250)
    assert counts() == (full_before, rebuild_before), "unticking must restore the plan"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_detail_header_is_clickable_to_collapse(page):
    """The whole Detail header collapses the pane, not just the chevron."""
    body = page.locator("#body")
    assert "no-detail" not in (body.get_attribute("class") or "")
    page.locator("#detailHead h2").click()          # click the title text, not the button
    assert "no-detail" in (body.get_attribute("class") or "")
    page.locator("#showDetail").click()
    assert "no-detail" not in (body.get_attribute("class") or "")


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_collapsing_panes_then_working_still_functions(page):
    page.locator("#hideTree").click()
    page.locator("#hideDetail").click()
    page.select_option("#modelPick", label="int_38")      # work with both panes hidden
    page.wait_for_selector("#graph svg .node")
    page.locator("#showDetail").click()
    assert "int_38" in page.locator("#results").inner_text(), "detail must catch up after reopening"
    page.locator("#showTree").click()
    assert page.locator('#treeBody .tree-item[aria-current="true"]').count() == 1


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_search_select_clear_keeps_selection(page):
    page.fill("#treeSearch", "int_38")
    page.locator("#treeBody .tree-item").first.click()
    page.wait_for_selector("#graph svg .node")
    page.fill("#treeSearch", "")                          # clear the filter
    assert page.locator('#treeBody .tree-item[aria-current="true"]').count() == 1
    assert "int_38" in page.locator("#results").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_node_labels_are_not_clipped(page):
    """mermaid sizes each box by measuring its label, so anything added AFTER by
    stylesheet (::after text, borders) overflows and clips the name. Assert no
    label overflows its box, in the busiest mode (names + type + columns)."""
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="impact"]').click()
    pick_col(page, "val1")
    page.wait_for_selector("#graph svg .node")

    overflowing = page.evaluate("""() => {
      const bad = [];
      document.querySelectorAll('#graph .node foreignObject:not(.rolebadge) div').forEach(d => {
        if (d.scrollWidth > d.clientWidth + 2 || d.scrollHeight > d.clientHeight + 2)
          bad.push((d.textContent || '').trim().slice(0, 40));
      });
      return bad;
    }""")
    assert not overflowing, f"clipped node labels: {overflowing}"

    # the full model name must be present, not truncated
    labels = " ".join(page.locator("#graph svg .node").all_text_contents())
    assert "stg_raw_app_t0" in labels
    assert page.locator("#graph .rb-target").count() == 1, "the target model is marked with a TARGET badge"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_help_panel_explains_the_controls(page):
    assert page.locator("#help").is_hidden()
    page.locator("#helpBtn").click()
    text = page.locator("#help .card").inner_text().lower()
    for topic in ("lineage", "impact", "columns", "incremental",
                  "additive", "refresh these first"):
        assert topic in text, f"help should explain {topic}"
    page.keyboard.press("Escape")
    assert page.locator("#help").is_hidden()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_changed_model_is_in_its_own_refresh_plan(page):
    """If the model you're changing is incremental, IT needs a full refresh too —
    a plain run only appends, leaving its existing rows on the old logic."""
    page.select_option("#modelPick", label="int_38")   # incremental
    page.locator('#modeSeg button[data-mode="impact"]').click()
    page.wait_for_selector("#graph svg .node")
    text = page.locator("#results").inner_text()
    full_section = text.split("NEEDS FULL REFRESH")[1].split("REBUILD NORMALLY")[0]
    assert "int_38" in full_section, "the changed incremental must be in the full-refresh list"
    assert "DROP TABLE" in text and "int_38" in text.split("DROP FIRST")[1]


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_tree_search_filters(page):
    page.fill("#treeSearch", "mart")
    names = page.locator("#treeBody .tree-item").all_inner_texts()
    assert names and all("mart" in n for n in names)


# ---------------------------------------------------------------------------
# Real-project checks. The synthetic fixture writes one column per line in a
# ~15-line model, which hides several things these exercise: multi-line
# expressions inside CTEs, highlights far below the fold, the unresolved-model
# nudge, both-direction column tracing, and the unproven-column marker.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_app_file(tmp_path_factory):
    """CountMoney built as-is (with catalog.json if present locally)."""
    from dbt_walker import cli

    if not (COUNTMONEY / "target" / "manifest.json").exists():
        pytest.skip(f"real fixture missing — build it with:\n    {FETCH_CMD}")
    out = tmp_path_factory.mktemp("real_app") / "app.html"
    cli.main(["build-app", str(COUNTMONEY), "--out", str(out)])
    return out


@pytest.fixture
def real_page(real_app_file):
    errors = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(real_app_file.as_uri())
        pg.wait_for_load_state("networkidle")
        yield pg
        browser.close()
    assert not errors, f"browser console errors: {errors}"


@pytest.fixture(scope="module")
def real_app_no_catalog(tmp_path_factory):
    """CountMoney built WITHOUT catalog.json, so the `select *` models stay
    unresolved. The catalog (if present locally) is hidden for the build, since
    with it every model resolves and there is nothing to nudge about."""
    import shutil

    from dbt_walker import cli

    if not (COUNTMONEY / "target" / "manifest.json").exists():
        pytest.skip(f"real fixture missing — build it with:\n    {FETCH_CMD}")
    catalog = COUNTMONEY / "target" / "catalog.json"
    stashed = catalog.with_suffix(".json.hidden_for_test")
    moved = catalog.exists()
    if moved:
        shutil.move(str(catalog), str(stashed))
    try:
        out = tmp_path_factory.mktemp("real_app_nocat") / "app.html"
        cli.main(["build-app", str(COUNTMONEY), "--out", str(out)])
    finally:
        if moved:
            shutil.move(str(stashed), str(catalog))
    return out


@pytest.fixture
def nocat_page(real_app_no_catalog):
    errors = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(real_app_no_catalog.as_uri())
        pg.wait_for_load_state("networkidle")
        yield pg
        browser.close()
    assert not errors, f"browser console errors: {errors}"


def _open_real_column(pg, model="stock_picks", column="current_ratio"):
    pg.select_option("#modelPick", label=model)
    pg.click("text=Columns")
    pg.wait_for_selector("#graph svg .node")
    pick_col(pg, column)
    pg.wait_for_timeout(600)
    pg.locator("#graph svg .node", has_text=model).first.click()
    pg.wait_for_selector("#sqlPanel .sqlbox")


@needs(COUNTMONEY, FETCH_CMD)
def test_real_model_highlights_whole_cte_expression(real_page):
    """current_ratio is a 9-line CASE inside a CTE; the model ends `select *`,
    so only CTE-aware spans find it. Alias matching would light just `as
    current_ratio`, and the original bug lit every line naming the column."""
    _open_real_column(real_page)
    hl = real_page.eval_on_selector_all(
        "#sqlPanel .sqlbox span.hl", "els => els.map(e => e.innerText)")
    assert len(hl) > 1, "a multi-line expression must highlight more than its alias line"
    joined = " ".join(hl)
    assert "case" in joined.lower() and "as current_ratio" in joined
    # and nothing that merely READS the column
    assert not any("total_cur_liab is null" in h and "as current_ratio" in h for h in hl[:1])


@needs(COUNTMONEY, FETCH_CMD)
def test_highlight_is_scrolled_into_view(real_page):
    """On a 180-line model the interesting lines are far below the fold; landing
    at line 1 with the highlight offscreen makes the feature invisible."""
    _open_real_column(real_page)
    real_page.wait_for_timeout(300)
    pos = real_page.evaluate("""() => {
      const box = document.querySelector('#sqlPanel .sqlbox');
      const hl = document.querySelector('#sqlPanel .sqlbox span.hl');
      return {scroll: box.scrollTop, top: hl.offsetTop, h: box.clientHeight};
    }""")
    assert pos["top"] > pos["h"], "fixture must actually need scrolling for this to mean anything"
    assert pos["scroll"] > 0, "panel did not scroll to the highlight"
    assert pos["scroll"] <= pos["top"] <= pos["scroll"] + pos["h"], "highlight not visible"


@needs(COUNTMONEY, FETCH_CMD)
def test_unresolved_model_nudges_toward_a_remedy(nocat_page):
    """A `select *` model's columns can't be traced (no catalog) -- instead of a
    bare 'lineage unresolved', an info affordance explains why and points at
    `dbt docs generate`. A resolved model shows no such affordance."""
    nocat_page.click("text=Columns")
    nocat_page.select_option("#modelPick", label="int_balance_sheet_latest")
    nocat_page.wait_for_timeout(300)
    assert nocat_page.locator("#colHint").is_visible(), "unresolved model needs the nudge"
    tip = nocat_page.locator("#colHint").get_attribute("data-tip")
    assert "dbt docs generate" in tip and "select *" in tip

    # a fully-resolved model must NOT show it
    nocat_page.select_option("#modelPick", label="stg_tushare_stock_basic")
    nocat_page.wait_for_timeout(300)
    assert nocat_page.locator("#colHint").is_hidden(), "resolved model should not nudge"


@needs(COUNTMONEY, FETCH_CMD)
@needs_catalog(COUNTMONEY)
def test_column_graph_shows_both_directions_and_labels(real_page):
    """Selecting a column annotates upstream nodes (which columns FEED it) and
    downstream nodes (which columns derive FROM it), with Model:/Type:/Columns:
    field labels, and groups the detail pane by source column. Needs the catalog
    so int_balance_sheet_latest resolves."""
    pg = real_page
    pg.select_option("#modelPick", label="int_balance_sheet_latest")
    pg.click("text=Columns")
    pg.wait_for_selector("#graph svg .node")
    pick_col(pg, "lt_borr")
    pg.wait_for_timeout(600)

    labels = " | ".join(pg.locator("#graph svg .node").all_text_contents())
    assert "&" not in labels, f"escaping artifact: {labels[:160]}"
    assert "Model:" in labels and "Type:" in labels and "Columns:" in labels
    assert "feeds" in labels, "upstream nodes should show which columns feed the selection"
    assert "from" in labels, "downstream nodes should show which source column they derive from"

    # detail pane groups downstream by the source column
    results = pg.locator("#results").inner_text()
    assert "by source column" in results.lower()
    assert "lt_borr" in results and "insolvent_index" in results


@needs(COUNTMONEY, FETCH_CMD)
def test_untraceable_columns_are_labelled(nocat_page):
    """Without a catalog, stock_picks' columns fail closed; the panel flags them
    as unproven rather than presenting them as proven findings. (Uses the
    no-catalog build -- with a catalog these columns resolve and aren't unproven.)"""
    nocat_page.select_option("#modelPick", label="int_income_pivoted_to_stock")
    nocat_page.click("text=Columns")
    nocat_page.wait_for_selector("#graph svg .node")
    pick_col(nocat_page, "last_end_date")
    nocat_page.wait_for_timeout(700)
    nocat_page.locator("#graph svg .node", has_text="stock_picks").first.click()
    nocat_page.wait_for_selector("#sqlPanel .sqlbox")

    panel = nocat_page.locator("#sqlPanel")
    assert panel.locator(".pill.unknown").count() > 0, "untraceable columns need a label"
    assert "could not be traced" in panel.inner_text()
    # and the SQL highlight is the hatched variant, not the solid "proven" one
    assert nocat_page.locator("#sqlPanel .sqlbox span.hl.unproven").count() > 0
    assert nocat_page.locator("#sqlPanel .sqlbox span.hl:not(.unproven)").count() == 0, (
        "nothing here is proven, so nothing should carry the solid highlight")


@needs(COUNTMONEY, FETCH_CMD)
def test_proven_columns_are_not_labelled(real_page):
    """The marker must not cry wolf: a fully traced model shows no unproven pill."""
    real_page.select_option("#modelPick", label="stg_tushare_stock_basic")
    real_page.click("text=Columns")
    real_page.wait_for_selector("#graph svg .node")
    real_page.locator("#colBtn").click()
    real_page.wait_for_selector("#colPop:not([hidden])")
    cols = real_page.eval_on_selector_all("#colAvail .colpop-item", "els => els.map(e => e.dataset.col)")
    assert len(cols) > 1, "fixture should expose traced columns here"
    real_page.keyboard.press("Escape")
    pick_col(real_page, cols[1])
    real_page.wait_for_timeout(500)
    real_page.locator("#graph svg .node", has_text="stg_tushare_stock_basic").first.click()
    real_page.wait_for_selector("#sqlPanel .sqlbox")
    assert "could not be traced" not in real_page.locator("#sqlPanel").inner_text()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_collapsing_tree_keeps_the_graph_visible(page):
    """Regression: hiding the tree (the first grid item) with display:none let the
    graph shift into the 0-width column, collapsing it. Panes are now pinned to
    their grid columns, so the graph keeps its width when the tree collapses."""
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    before = page.locator("main.graph").evaluate("e => e.getBoundingClientRect().width")
    assert before > 400
    page.locator("#hideTree").click()
    page.wait_for_timeout(300)
    after = page.locator("main.graph").evaluate("e => e.getBoundingClientRect().width")
    assert after > before, f"graph must GROW when the tree collapses, not vanish (was {before}, now {after})"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_inspected_node_gets_light_blue_stroke(page):
    """Clicking a (non-target) node rings it light blue (not mermaid's muted
    default, which an external stylesheet can't override past its inline
    !important), and the INSPECTING badge + in-node hint appear."""
    CYAN = "rgb(34, 211, 238)"
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    page.locator("#graph svg .node", has_text="int_39").first.click()
    page.wait_for_timeout(200)
    stroke = page.evaluate(
        "() => getComputedStyle(document.querySelector('#graph .node.selected rect')).stroke")
    assert stroke == CYAN, f"selection should be light blue, got {stroke}"
    assert page.locator("#graph .rb-inspect").count() == 1, "INSPECTING badge expected"
    assert page.locator("#graph .selhint").count() == 1, "in-node 'make target' hint expected"
    # deselecting restores the original stroke, not leaving cyan stuck on
    page.locator("#graph svg .node", has_text="int_30").first.click()
    page.wait_for_timeout(200)
    restored = page.evaluate("""() => {
      const g = [...document.querySelectorAll('#graph .node')].find(n => /int_39/.test(n.textContent));
      return getComputedStyle(g.querySelector('rect')).stroke;
    }""")
    assert restored != CYAN, "deselected node must restore its original stroke"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_focus_button_refocuses_the_graph(page):
    """Clicking a node then its 'focus this model' button re-roots the graph on it,
    exactly like picking it from the tree."""
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    page.locator("#graph svg .node", has_text="int_39").first.click()
    page.wait_for_selector("#focusBtn")
    page.locator("#focusBtn").click()
    page.wait_for_timeout(300)
    assert page.evaluate("() => N[S.model].name") == "int_39"
    assert page.locator("#modelPick").input_value().endswith("int_39")


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_two_pane_column_picker(page):
    """The picker stays open across multiple adds (click Available to add, click
    Selected to remove), filters, and closes on outside click."""
    page.select_option("#modelPick", label="stg_raw_app_t0")
    page.locator('#modeSeg button[data-mode="columns"]').click()
    page.wait_for_selector("#graph svg .node")

    page.locator("#colBtn").click()
    page.wait_for_selector("#colPop:not([hidden])")
    avail = page.eval_on_selector_all("#colAvail .colpop-item", "els => els.map(e => e.dataset.col)")
    assert len(avail) >= 2, "model should expose several columns"
    a, b = avail[0], avail[1]

    page.locator(f'#colAvail .colpop-item[data-col="{a}"]').click()
    page.locator(f'#colAvail .colpop-item[data-col="{b}"]').click()
    assert not page.locator("#colPop").is_hidden(), "picker must stay open across adds"
    assert set(page.evaluate("() => S.cols")) == {a, b}
    assert page.locator("#colBtn").inner_text() == "columns (2)"

    # remove one from the Selected pane
    page.locator(f'#colSel .colpop-item[data-col="{a}"]').click()
    page.wait_for_timeout(120)
    assert page.evaluate("() => S.cols") == [b]

    # filter narrows the Available list
    page.fill("#colPopFilter", b)
    page.wait_for_timeout(100)
    shown = page.eval_on_selector_all("#colAvail .colpop-item", "els => els.map(e => e.dataset.col)")
    assert all(b in c for c in shown)

    # outside click closes it
    page.mouse.click(page.viewport_size["width"] // 2, page.viewport_size["height"] - 60)
    page.wait_for_timeout(120)
    assert page.locator("#colPop").is_hidden()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_target_inspecting_roles_and_legend(page):
    """TARGET badge on the picked model; clicking another node adds an INSPECTING
    badge + cyan ring + Detail-header echo; clicking the target itself wraps a
    ring around it (both badges); the legend collapses to a pill and back."""
    page.select_option("#modelPick", label="mart_0")
    page.wait_for_selector("#graph svg .node")
    assert page.locator("#graph .rb-target").count() == 1
    assert page.locator("#legend .card").is_visible(), "legend shows expanded by default"

    # inspect a different node
    page.locator("#graph svg .node", has_text="int_39").first.click()
    page.wait_for_timeout(200)
    assert page.locator("#graph .rb-inspect").count() == 1
    assert page.locator("#graph .selring").count() == 0, "ring only when the target itself is selected"
    assert "inspecting" in (page.locator("#detailHead").get_attribute("class") or "")
    assert "INSPECTING" in page.locator("#detailHead h2").inner_text()

    # inspect the target node itself -> ring + both badges, no in-node hint
    page.locator("#graph svg .node", has_text="mart_0").first.click()
    page.wait_for_timeout(200)
    assert page.locator("#graph .selring").count() == 1, "selection ring wraps the target"
    assert page.locator("#graph .rb-target").count() == 1
    assert page.locator("#graph .rb-inspect").count() == 1

    # legend collapse -> pill, and back
    page.locator("#legend .lg-toggle").click()
    page.wait_for_timeout(100)
    assert page.locator("#legend .pill").is_visible()
    assert page.locator("#legend .card").count() == 0
    page.locator("#legend .pill").click()
    page.wait_for_timeout(100)
    assert page.locator("#legend .card").is_visible()


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_collapsed_side_tabs_are_tall(page):
    """The reveal tabs for collapsed panes should be tall/noticeable, not tiny."""
    page.locator("#hideTree").click()
    box = page.locator("#showTree").bounding_box()
    assert box["height"] >= 80, f"reveal tab should be tall, got {box['height']}"
