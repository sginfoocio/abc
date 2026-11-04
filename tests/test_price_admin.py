"""Administration and scoped batches use synthetic catalogues and transports only."""
import json

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app_pages.price_watch import ADMIN_SECTIONS
from price_batch import WatchBatch, observation_export
from price_watch import PriceWatch
from tests.test_price_daily import _daily_env
from tests.test_price_locator import Site, _env, _gtin, _watch
from tests.test_price_monitor import _edit, _monitor_env


def _dump(store):
    with store._connect() as con:
        return "\n".join(con.iterdump())


def test_preview_is_read_only_and_prepare_is_idempotent_without_http(tmp_path):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path, count=2)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    eans = PriceWatch(store).watchlist()["ean"].tolist()
    before = _dump(store)
    preview = batch.preview(eans, [cid])
    assert _dump(store) == before and not site.calls
    assert preview["located"].all() and set(preview["action"]) == {"Monitorizar"}
    batch.prepare(eans[:1], [cid])
    assert monitor.jobs()["ean"].tolist() == eans[:1]
    after = _dump(store)
    batch.prepare(eans[:1], [cid])
    assert _dump(store) == after and not site.calls


def test_batch_50_by_six_sync_first_and_scope_selection(tmp_path):
    store, _, locator, monitor, _, _, ids, shops, _, rows, events = _daily_env(tmp_path, 50)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    eans = [r[0] for r in rows]
    preview = batch.preview(eans, ids)
    assert len(preview) == 300 and preview["ready"].all()
    assert not events
    result = batch.execute(eans[:2], ids[:2], approved=True)
    assert len(result["runs"]) == 2 and result["vytria"]["monitored"] == 50
    assert events[0][0] == "vytria"
    assert len(monitor.jobs()) == 4 and monitor.jobs()["state"].eq("done").all()
    assert set(locator.jobs().query("state == 'done'")["ean"]) == set(eans[:2])
    assert not any(shop.calls for cid, shop in shops.items() if cid not in ids[:2])
    assert len(observation_export(store)) == 4
    batch.execute(eans, ids, approved=True, sync_vytria=False)
    assert len(monitor.jobs()) == 300 and monitor.jobs()["state"].eq("done").all()
    listing_reads = sum(s.count("/marca/") for s in shops.values())
    events.clear()
    again = batch.execute(eans, ids, approved=True, sync_vytria=False)
    assert not again["runs"] and not events
    assert sum(s.count("/marca/") for s in shops.values()) == listing_reads


def test_scoped_locator_bulk_stop_does_not_change_other_jobs(tmp_path):
    store, access, locator, _, cid = _env(tmp_path, Site())
    eans = [_gtin(f"843{i:09d}") for i in range(2)]
    _watch(store, [(ean, "Alpha", "AL1000", f"00{i}", "") for i, ean in enumerate(eans)])
    access.set_method(cid, "ficha", "denied", actor="test", note="No access")
    with store._connect() as con:
        before = tuple(con.execute("SELECT * FROM locate_jobs WHERE ean=? AND competitor_id=?", (eans[1], cid)).fetchone())
    locator.run([cid], eans=eans[:1])
    with store._connect() as con:
        assert tuple(con.execute("SELECT * FROM locate_jobs WHERE ean=? AND competitor_id=?", (eans[1], cid)).fetchone()) == before
        assert con.execute("SELECT state FROM locate_jobs WHERE ean=? AND competitor_id=?", (eans[0], cid)).fetchone()[0] == "unavailable"
    assert locator.run([], eans=eans).processed == 0
    assert locator.run([cid], eans=[]).processed == 0


@pytest.mark.parametrize("kind", ["permission", "budget", "captcha"])
def test_batch_revalidates_current_restrictions(tmp_path, kind):
    store, access, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    eans = PriceWatch(store).watchlist()["ean"].tolist()
    batch.prepare(eans, [cid])
    assert batch.preview(eans, [cid])["ready"].all()
    if kind == "permission":
        access.set_method(cid, "ficha", "denied", actor="test", note="Revoked")
    elif kind == "budget":
        access.set_budget("competitor", cid, daily_limit=0, per_minute_limit=600, pause_seconds=0, actor="test")
    else:
        access.report_block("competitor", cid, method="ficha", kind="captcha", url=site.base, detail="Fixture")
    assert not batch.preview(eans, [cid])["ready"].any()
    assert batch.execute(eans, [cid], approved=True, sync_vytria=False)["runs"] == []
    assert not site.calls


def test_selection_and_explicit_confirmation_are_required(tmp_path):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    eans = PriceWatch(store).watchlist()["ean"].tolist()
    before = _dump(store)
    for selected, competitors in (([], [cid]), (eans, []), (["invalid"], [cid])):
        with pytest.raises(ValueError):
            batch.prepare(selected, competitors)
    with pytest.raises(ValueError, match="confirmación"):
        batch.execute(eans, [cid], approved=False)
    assert _dump(store) == before and not site.calls


def test_batch_pair_limit_is_shared_by_both_queues(tmp_path):
    store, _, locator, monitor, _, _, ids, _, _, rows, _ = _daily_env(tmp_path, 3)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    result = batch.execute([r[0] for r in rows], ids[:2], approved=True, sync_vytria=False, max_jobs=2)
    assert monitor.jobs()["state"].eq("done").sum() == 2
    assert locator.jobs()["state"].eq("done").sum() == 2
    assert result["deferred"] == 4
    assert not result["skipped"]


def test_pilot_pairs_are_not_executed(tmp_path, monkeypatch):
    store, _, locator, monitor, _, _, ids, _, _, rows, events = _daily_env(tmp_path, 2)
    eans = [r[0] for r in rows]
    monkeypatch.setattr("price_batch.pilot_scope", lambda store: {"eans": eans, "competitors": ids})
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    preview = batch.preview(eans, ids)
    assert set(preview["action"]) == {"Pendiente en piloto"}
    assert not preview["ready"].any()
    result = batch.execute(eans, ids, approved=True, sync_vytria=False)
    assert not result["runs"] and len(result["skipped"]) == 12
    assert not events


def test_pending_identity_and_empty_scoped_monitor_do_not_issue_http(tmp_path):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path)
    eans = PriceWatch(store).watchlist()["ean"].tolist()
    with store._connect() as con, con:
        con.execute("UPDATE competitor_product_urls SET status='pending_verification' WHERE competitor_id=?", (cid,))
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    preview = batch.preview(eans, [cid])
    assert set(preview["action"]) == {"Revisión / recuperación"}
    assert not batch.execute(eans, [cid], approved=True, sync_vytria=False)["runs"]
    before = _dump(store)
    assert monitor.run([cid], eans=[]).processed == 0
    assert _dump(store) == before and not site.calls


def test_observation_export_preserves_original_history_and_provenance(tmp_path):
    store, _, _, monitor, clock, cid, site = _monitor_env(tmp_path)
    monitor.run([cid])
    ean = monitor.jobs().iloc[0]["ean"]
    url = monitor.jobs().iloc[0]["url"]
    # Existing pilot approval of the same URL is evidence of reuse, not a second offer.
    with store._connect() as con, con:
        run = con.execute("""INSERT INTO pilot_runs(started_at,status,scope,competitors)
                             VALUES(?,'done','[]','[]')""", (clock().isoformat(),)).lastrowid
        con.execute("""INSERT INTO pilot_url_candidates(ean,competitor_id,url,state,run_id,method,evidence,
                       identity,checked_at) VALUES(?,?,?,'confirmed',?,'catalog','{}','{}',?)""",
                    (ean, cid, url, run, clock().isoformat()))
    _edit(site, monitor, price=130)
    clock.advance(hours=25)
    monitor.run([cid])
    site.calls.clear()
    before = _dump(store)
    result = observation_export(store)
    assert result.iloc[0]["provenance"] == "Piloto: ficha reutilizada"
    assert monitor.inventory().iloc[0]["provenance"] == "Piloto: ficha reutilizada"
    assert {"ean", "brand", "model", "color", "competitor", "url", "price", "availability",
            "price_includes_tax", "vat_rate", "captured_at", "checked_at", "match_status", "method",
            "monitor_state", "valid_until", "url_source"} <= set(result.columns)
    assert _dump(store) == before
    assert len(result) == 2 and not site.calls
    latest, previous = result.iloc[0], result.iloc[1]
    assert latest["valid_until"] and latest["checked_at"]
    assert pd.isna(previous["valid_until"]) and previous["checked_at"] == previous["captured_at"]


def test_six_sections_and_all_competitor_controls_are_offline(tmp_path, monkeypatch):
    store, _, _, monitor, _, _, _ = _monitor_env(tmp_path)
    monitor.run()
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("No real HTTP"))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                             "render_price_comparator_page()").run(timeout=30)
    assert app.selectbox(key="price_admin_section").options == list(ADMIN_SECTIONS)
    for section in ADMIN_SECTIONS:
        app.selectbox(key="price_admin_section").set_value(section).run(timeout=30)
        assert not app.exception, section
        if section == "Competidores":
            for task in app.selectbox(key="competitor_card_section").options:
                app.selectbox(key="competitor_card_section").set_value(task).run(timeout=30)
                assert not app.exception, task


def test_monitor_metrics_respect_every_filter_and_reading_does_not_modify_data(tmp_path, monkeypatch):
    store, _, _, monitor, clock, cid, _ = _monitor_env(tmp_path, count=3)
    monitor.run([cid], max_jobs=2)
    with store._connect() as con, con:
        rows = con.execute("SELECT id,sample FROM monitor_jobs WHERE state='done' ORDER BY id").fetchall()
        sample = json.loads(rows[1]["sample"])
        sample["page"]["availability"] = "unavailable"
        con.execute("UPDATE monitor_jobs SET sample=?,eligible=0 WHERE id=?", (json.dumps(sample), rows[1]["id"]))
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    script = ("from app_pages.price_watch import _render_monitor\n"
              "from price_comparator import PriceComparatorStore\n"
              f"_render_monitor(PriceComparatorStore(), {{{cid!r}: 'Visio Factory'}}, advanced=False)")
    before = _dump(store)
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Actualizado"] == "1" and metrics["Sin stock"] == "1"
    app.multiselect(key="monitor_status").set_value(["Sin stock"]).run(timeout=30)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Actualizado"] == "0" and metrics["Sin stock"] == "1"
    app.text_input(key="monitor_query").set_value("does-not-exist").run(timeout=30)
    assert all(m.value == "0" for m in app.metric)
    assert _dump(store) == before
    assert not app.exception


def test_batch_ui_requires_prepare_and_confirmation_and_resets_on_scope_change(tmp_path, monkeypatch):
    store, _, locator, monitor, _, cid, site = _monitor_env(tmp_path, count=2)
    batch = WatchBatch(store, locator=locator, monitor=monitor)
    monkeypatch.setattr("price_batch.WatchBatch", lambda store: batch)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    eans = PriceWatch(store).watchlist()["ean"].tolist()
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_batch, _notices\n"
        "from price_comparator import PriceComparatorStore\n"
        "_notices()\n"
        f"_render_batch(PriceComparatorStore(), {{{cid!r}: 'Visio Factory'}})").run(timeout=30)
    app.multiselect(key="batch_eans").set_value(eans[:1])
    app.multiselect(key="batch_competitors").set_value([cid]).run(timeout=30)
    assert app.button(key="batch_execute").disabled
    app.button(key="batch_prepare").click().run(timeout=30)
    assert app.button(key="batch_execute").disabled and not site.calls
    app.checkbox(key="batch_approved").check().run(timeout=30)
    assert not app.button(key="batch_execute").disabled
    app.multiselect(key="batch_eans").set_value(eans).run(timeout=30)
    assert app.button(key="batch_execute").disabled and not app.checkbox(key="batch_approved").value
    assert not site.calls
    app.button(key="batch_prepare").click().run(timeout=30)
    app.checkbox(key="batch_sync").uncheck().run(timeout=30)
    app.checkbox(key="batch_approved").check().run(timeout=30)
    app.number_input(key="batch_max").set_value(2).run(timeout=30)
    assert app.button(key="batch_execute").disabled
    app.checkbox(key="batch_approved").check().run(timeout=30)
    app.button(key="batch_execute").click().run(timeout=30)
    assert not app.exception
    assert monitor.jobs()["state"].eq("done").sum() == 2
    assert app.button(key="batch_execute").disabled
    assert not app.checkbox(key="batch_approved").value
    assert not any("/marca/" in url for url in site.calls)


def test_web_dates_and_error_labels_do_not_display_nan(tmp_path, monkeypatch):
    store, _, _, _, _, cid, _ = _monitor_env(tmp_path)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    app = AppTest.from_string(
        "from price_comparator import PriceComparatorStore\n"
        "from price_web import WebPriceSearch\n"
        "from app_pages.price_comparator import _render_web_section\n"
        "search=WebPriceSearch(PriceComparatorStore())\n"
        f"row=search.configs().set_index('competitor_id', drop=False).loc[{cid!r}].to_dict()\n"
        "row.update(blocked_at=float('nan'),last_error=float('nan'),last_query_at=None,robots_summary=None,pause_seconds=1.0)\n"
        "_render_web_section(search,row)").run(timeout=30)
    assert not app.exception
    rendered = " ".join(str(item.value) for item in [*app.markdown, *app.caption])
    assert "bloqueado el nan" not in rendered and "último error: nan" not in rendered
    assert "robots.txt sin comprobar" in rendered and "Última consulta: —" in rendered
