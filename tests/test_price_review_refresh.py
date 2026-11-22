"""Durable manual-review confirmation and immediate price reads; all HTTP is synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Event

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app_pages.price_watch import _review_expected_candidate
from price_monitor import PriceMonitor
from price_access import AccessControl
from tests.test_price_locator import ROBOTS, Site, _env, _gtin, _watch
from tests.web_fixtures import page


def _review_env(tmp_path, *, count=1, stock=True, gtin_override=None, robots=ROBOTS):
    site = Site(robots=robots)
    store, _, locator, clock, cid = _env(tmp_path, site)
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=(id=?)", (cid,))
        con.execute("UPDATE competitors SET market='ES',currency='EUR' WHERE id=?", (cid,))
        con.execute("UPDATE competitor_web_config SET tax_policy='included' WHERE competitor_id=?", (cid,))
    rows = []
    urls = []
    for index in range(count):
        ean = "0805289126577" if index == 0 else _gtin(f"8410000000{index + 1:02d}")
        model = f"AL{1000 + index}"
        rows.append((ean, "Alpha", model, "93E", "50-18"))
        url = site.product("Alpha", model, "93E", caliber="58-20",
                           gtin=gtin_override or ean, stock=stock)
        urls.append(url)
    _watch(store, rows)
    locator.enqueue([cid], [row[0] for row in rows])
    now = clock().isoformat()
    with store._connect() as con, con:
        for (ean, brand, model, color, _), url in zip(rows, urls):
            con.execute("""INSERT INTO competitor_product_urls(
                competitor_id,ean,url,model_code,color_code,match_status,status,verified_at,source,note)
                VALUES(?,?,?,?,?,'probable_match','probable',?,'auto_watch','ambiguous candidate')""",
                        (cid, ean, url, model, color, now))
            con.execute("""UPDATE locate_jobs SET state='review',url=?,detail='ambiguous candidate',updated_at=?
                WHERE competitor_id=? AND ean=?""", (url, now, cid, ean))
    return store, locator, PriceMonitor(store, transport=site, clock=clock, sleep=lambda _: None), clock, cid, site, rows, urls


def _expected(row):
    fields = ("candidate_status", "match_status", "model_code", "color_code", "caliber", "ean_published",
              "candidate_source", "candidate_note", "candidate_verified_at", "job_state", "job_url",
              "job_updated_at", "watch_active", "competitor_active")
    return {"job_id": int(row["id"]), **{
        name: (row[name].item() if hasattr(row[name], "item") else row[name]) for name in fields
    }}


def _candidate(locator, ean):
    rows = locator.jobs(states=("review",), limit=None)
    return rows.loc[rows["ean"].eq(ean)].iloc[0]


def test_preview_snapshot_accepts_candidate_indexed_by_job_id(tmp_path):
    _, locator, _, _, _, _, rows, _ = _review_env(tmp_path)
    review = locator.jobs(states=("review",), limit=None).set_index("id")
    candidate = review.loc[int(_candidate(locator, rows[0][0])["id"])]
    assert _review_expected_candidate(candidate, job_id=candidate.name)["job_id"] == candidate.name


def test_review_grid_filters_selection_and_preview_stay_explicit(tmp_path):
    store = _review_env(tmp_path, count=3)[0]
    names = dict(zip(store.competitors()["id"], store.competitors()["name"]))
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_locator\n"
        "from price_comparator import PriceComparatorStore\n"
        f"store = PriceComparatorStore({str(store.database_path)!r})\n"
        f"names = {names!r}\n"
        "_render_locator(store, names)\n"
    ).run()
    assert not app.exception

    app.text_input(key="review_model_filter").set_value("AL1000").run()
    filtered_metric = next(metric for metric in app.metric if metric.label == "Resultados filtrados")
    assert filtered_metric.value == "1"
    app.button(key="review_select_filtered").click().run()
    selected_metric = next(metric for metric in app.metric if metric.label == "Filas seleccionadas")
    assert selected_metric.value == "1"
    app.button(key="review_prepare_bulk").click().run()
    assert any("Vista previa: 1 fichas seleccionadas" in item.value for item in app.markdown)

    app.text_input(key="review_model_filter").set_value("AL1001").run()
    selected_metric = next(metric for metric in app.metric if metric.label == "Filas seleccionadas")
    assert selected_metric.value == "0"
    assert any("Se limpió la selección" in item.value for item in app.warning)
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM competitor_product_urls WHERE status='probable'").fetchone()[0] == 3


def test_individual_review_ui_records_acceptance_and_refresh_without_http(tmp_path):
    store, _, _, _, _, site, _, _ = _review_env(tmp_path)
    names = dict(zip(store.competitors()["id"], store.competitors()["name"]))
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_locator\n"
        "from price_comparator import PriceComparatorStore\n"
        f"store = PriceComparatorStore({str(store.database_path)!r})\n"
        f"names = {names!r}\n"
        "_render_locator(store, names)\n"
    ).run()
    app.text_input(key="locate_review_note").set_value("Validación manual")
    next(button for button in app.button
         if button.label == "Confirmar ficha y actualizar precio").click().run()
    assert not app.exception
    with store._connect() as con:
        assert con.execute("SELECT status FROM competitor_product_urls").fetchone()[0] == "verified"
        refresh = con.execute("SELECT id,actor,reason,status FROM manual_review_refreshes").fetchone()
        assert refresh["actor"] == "admin" and refresh["reason"] == "Validación manual"
        assert refresh["status"] == "pending"
    assert any("Ficha aprobada" in str(table.value) and str(refresh["id"]) in str(table.value)
               for table in app.dataframe)
    assert site.calls == []


def test_review_request_reuses_normal_monitor_read_completed_after_acceptance(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    row = _candidate(locator, ean)
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Evitar consulta duplicada",
                          update_price=True, expected_candidate=_expected(row))

    clock.advance(microseconds=1)
    normal = monitor.run([cid], max_jobs=1, eans=[ean])
    assert normal.processed == 1
    before_calls = list(site.calls)
    with store._connect() as con:
        before_offers = con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?",
                                    (ean, cid)).fetchone()[0]

    result = monitor.review_refresh_tick()
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert result["processed"] == 1 and site.calls == before_calls
    assert progress["status"] == "done" and "Oferta comparable" in progress["detail"]
    assert progress["http_requests"] == 0
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?",
                           (ean, cid)).fetchone()[0] == before_offers == 1


def test_approval_reuses_a_valid_saved_read_without_waiting_for_its_due_date(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET status='verified',source='user'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, urls[0]))
        con.execute("""UPDATE locate_jobs SET state='done',url=?
            WHERE competitor_id=? AND ean=?""", (urls[0], cid, ean))
    clock.advance(seconds=1)
    assert monitor.run([cid], max_jobs=1, eans=[ean]).processed == 1
    before_calls = list(site.calls)
    with store._connect() as con:
        before_sampled_at = con.execute(
            "SELECT sampled_at FROM monitor_jobs WHERE competitor_id=? AND ean=?", (cid, ean)).fetchone()[0]
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET status='probable',source='auto_watch',
            match_status='probable_match' WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, urls[0]))
        con.execute("""UPDATE locate_jobs SET state='review',url=?,detail='ambiguous candidate'
            WHERE competitor_id=? AND ean=?""", (urls[0], cid, ean))

    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Revalidar ficha",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    assert monitor.review_refresh_tick()["processed"] == 1
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert progress["status"] == "done" and progress["http_requests"] == progress["attempts"] == 0
    assert "lectura original" in progress["detail"]
    assert site.calls == before_calls
    with store._connect() as con:
        assert con.execute("SELECT sampled_at FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                           (cid, ean)).fetchone()[0] == before_sampled_at
        audit = con.execute("""SELECT current FROM access_audit WHERE scope='manual_review'
            AND scope_id=? AND action='reuse_saved_sample'""", (str(task),)).fetchone()
    assert audit and json.loads(audit["current"])["sampled_at"] == before_sampled_at


def test_manual_review_reuses_shared_price_without_transferring_stock(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    source_ean, _, model, color, _ = rows[0]
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_product_urls SET status='verified',source='user'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, source_ean, urls[0]))
        con.execute("""UPDATE locate_jobs SET state='done',url=?
            WHERE competitor_id=? AND ean=?""", (urls[0], cid, source_ean))
    clock.advance(seconds=1)
    assert monitor.run([cid], max_jobs=1, eans=[source_ean]).processed == 1
    before_calls = list(site.calls)

    target_ean = _gtin("841999900001")
    _watch(store, [(target_ean, "Alpha", model, color, "54-20")])
    locator.enqueue([cid], [target_ean])
    with store._connect() as con, con:
        con.execute("""INSERT INTO competitor_product_urls(
            competitor_id,ean,url,model_code,color_code,match_status,status,verified_at,source,note)
            VALUES(?,?,?,?,?,'probable_match','probable',?,'auto_watch','ambiguous candidate')""",
                    (cid, target_ean, urls[0], model, color, clock().isoformat()))
        con.execute("""UPDATE locate_jobs SET state='review',url=?,detail='ambiguous candidate'
            WHERE competitor_id=? AND ean=?""", (urls[0], cid, target_ean))
    task = locator.review(target_ean, cid, urls[0], accept=True, actor="ana", note="Variante vinculada",
                          update_price=True, expected_candidate=_expected(_candidate(locator, target_ean)))

    result = monitor.review_refresh_tick()
    progress = monitor.review_refreshes().set_index("id").loc[task]
    with store._connect() as con:
        job = con.execute("SELECT sample,eligible FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                          (cid, target_ean)).fetchone()
        offers = con.execute("SELECT price,captured_at FROM offers WHERE competitor_id=? AND ean=?",
                             (cid, target_ean)).fetchall()
        audit = con.execute("""SELECT current FROM access_audit WHERE scope='manual_review'
            AND scope_id=? AND action='reuse_shared_sample'""", (str(task),)).fetchone()
    sample = json.loads(job["sample"])
    assert result["processed"] == 1 and progress["status"] == "done"
    assert progress["http_requests"] == progress["attempts"] == 0 and site.calls == before_calls
    assert sample["shared_source"]["ean"] == source_ean
    assert audit and json.loads(audit["current"])["stock_transferred"] is False
    assert sample["page"]["price"] == 120 and sample["page"]["availability"] == "unknown"
    assert sample["page"]["ean_published"] == "" and sample["page"]["variant_id"] == ""
    assert not job["eligible"] and len(offers) == 1 and offers[0]["price"] == 120


def test_approved_read_refreshes_at_existing_interval_without_reapproval(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Mantener precio actualizado",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    assert monitor.review_refresh_tick()["processed"] == 1
    with store._connect() as con:
        original = con.execute(
            "SELECT requested_at FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0]
        approved = con.execute("""SELECT status,source,verified_at FROM competitor_product_urls
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, urls[0])).fetchone()
    before_calls = site.calls.count(urls[0])

    clock.advance(hours=monitor.watch.monitor_interval_hours(ean, cid))
    own = pd.DataFrame([{"EAN": ean, "Precio": 150, "Moneda": "EUR", "Mercado": "ES",
                         "Fecha consulta": clock().isoformat(), "Origen": "Synthetic"}])
    assert store.import_own_prices(own).imported == 1
    result = monitor.review_refresh_tick()
    with store._connect() as con:
        request = con.execute("""SELECT status,requested_at,attempts,http_requests
            FROM manual_review_refreshes WHERE id=?""", (task,)).fetchone()
        copies = con.execute("SELECT COUNT(*) FROM manual_review_refreshes WHERE ean=? AND competitor_id=?",
                             (ean, cid)).fetchone()[0]
        samples = con.execute("""SELECT observation_id,sampled_at,sample_json FROM manual_review_recorded_sample_events
            WHERE refresh_id=? ORDER BY sampled_at""", (task,)).fetchall()
        still_approved = con.execute("""SELECT status,source,verified_at FROM competitor_product_urls
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, urls[0])).fetchone()
    assert result["processed"] == 1 and request["status"] == "done"
    assert request["requested_at"] == original and request["attempts"] == 2
    assert request["http_requests"] > 0 and copies == 1
    assert len(samples) == 2 and samples[0]["sampled_at"] == original
    assert samples[0]["observation_id"] != samples[1]["observation_id"]
    assert all(json.loads(sample["sample_json"])["page"]["price"] == 120 for sample in samples)
    assert tuple(still_approved) == tuple(approved)
    assert site.calls.count(urls[0]) == before_calls + 1
    assert monitor.inventory().query("ean == @ean")["comparable"].all()
    comparison = store.dashboard(now=clock())[0].query("ean == @ean").iloc[0]
    assert comparison["competitor_count"] == 1 and comparison["competitor_min_eur"] == 120
    assert len(store.current_offers(now=clock()).query("ean == @ean")) == 1
    assert store.current_offers(now=clock() + timedelta(days=2)).empty
    assert monitor.review_refresh_tick()["processed"] == 0
    assert site.calls.count(urls[0]) == before_calls + 1


def test_missing_price_does_not_trigger_a_tight_refresh_loop(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    site.pages[urls[0]] = page(name="Alpha AL1000 93E 58-20", price=None, brand="Alpha", gtin=ean)
    with store._connect() as con, con:
        con.execute("""UPDATE competitor_web_config SET sales_market='ES',market_verified=1,
            market_evidence_url=?,market_note='Synthetic policy' WHERE competitor_id=?""", (site.base, cid))
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Ficha sin precio",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    assert monitor.review_refresh_tick()["processed"] == 1
    calls = len(site.calls)
    assert monitor.review_refresh_tick()["processed"] == 0
    assert len(site.calls) == calls
    clock.advance(hours=monitor.watch.monitor_interval_hours(ean, cid))
    assert monitor.review_refresh_tick()["processed"] == 1
    assert monitor.review_refresh_tick()["processed"] == 0
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_sample_events WHERE refresh_id=?",
                           (task,)).fetchone()[0] == 2
        assert con.execute("SELECT status FROM competitor_product_urls WHERE competitor_id=? AND ean=?",
                           (cid, ean)).fetchone()[0] == "verified"
    assert site.calls.count(urls[0]) == 2


def test_two_review_workers_do_not_read_the_same_approved_url_twice(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Lectura concurrente",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    entered, release = Event(), Event()

    def transport(url, headers, timeout):
        if url == urls[0]:
            entered.set()
            assert release.wait(20)
        return site(url, headers, timeout)

    monitor._transport = transport
    other = PriceMonitor(store, transport=site, clock=clock, sleep=lambda _: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(monitor.review_refresh_tick)
        try:
            assert entered.wait(20)
            second = pool.submit(other.review_refresh_tick).result(timeout=20)
        finally:
            release.set()
        result = first.result(timeout=20)
    assert second["processed"] == 0 and second["skipped"]
    assert result["processed"] == 1 and site.calls.count(urls[0]) == 1
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_sample_events WHERE refresh_id=?",
                           (task,)).fetchone()[0] == 1


def test_review_request_does_not_retry_a_terminal_monitor_block(tmp_path):
    store, locator, monitor, _, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    row = _candidate(locator, ean)
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Respetar bloqueo vigente",
                          update_price=True, expected_candidate=_expected(row))
    with store._connect() as con, con:
        con.execute("""UPDATE monitor_jobs SET state='blocked',detail='Bloqueo de acceso'
            WHERE ean=? AND competitor_id=?""", (ean, cid))

    result = monitor.review_refresh_tick()
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert result["processed"] == 0 and result["errors"]
    assert progress["status"] == "blocked" and progress["detail"] == "Bloqueo de acceso"
    assert site.calls == []


def test_manual_acceptance_is_atomic_durable_and_uses_fresh_shared_monitor(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    assert ean == "0805289126577"
    assert _candidate(locator, ean)["ean"] == "0805289126577"
    row = _candidate(locator, ean)
    task_id = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Identidad revisada",
                             update_price=True, expected_candidate=_expected(row))
    assert task_id
    with store._connect() as con:
        accepted = con.execute("""SELECT status,source,note,verified_at FROM competitor_product_urls
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, urls[0])).fetchone()
        queued = con.execute("SELECT status,actor,reason,url FROM manual_review_refreshes WHERE id=?",
                             (task_id,)).fetchone()
        assert accepted["status"] == "verified" and accepted["source"] == "user"
        assert all(value in accepted["note"] for value in ("ana", "Identidad revisada"))
        assert accepted["verified_at"] == clock().isoformat(timespec="microseconds")
        assert queued["status"] == "pending" and queued["actor"] == "ana"
        assert queued["reason"] == "Identidad revisada" and queued["url"] == urls[0]
        assert con.execute("SELECT state FROM locate_jobs WHERE ean=? AND competitor_id=?",
                           (ean, cid)).fetchone()[0] == "done"

    # Simulate the app process stopping after the durable request was recorded.
    with store._connect() as con, con:
        con.execute("""UPDATE manual_review_refreshes SET status='running',lease_until=? WHERE id=?""",
                    ((clock() - timedelta(seconds=1)).isoformat(), task_id))
    restored = PriceMonitor(store, transport=site, clock=clock, sleep=lambda _: None)
    result = restored.review_refresh_tick()
    assert result["processed"] == 1
    assert urls[0] in site.calls
    with store._connect() as con:
        task = con.execute("SELECT status,http_requests,attempts,detail FROM manual_review_refreshes WHERE id=?",
                           (task_id,)).fetchone()
        job = con.execute("SELECT state,eligible,sample FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                          (cid, ean)).fetchone()
        assert task["status"] == "done" and task["http_requests"] > 0 and task["attempts"] == 1
        assert job["state"] == "done" and job["eligible"] == 1
        sample = json.loads(job["sample"])
        assert sample["page"]["price"] == 120
        assert "Oferta comparable" in task["detail"]
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=? AND competitor_id=?",
                           (ean, cid)).fetchone()[0] == 1
    before_calls = list(site.calls)
    assert restored.review_refresh_tick()["processed"] == 0
    assert site.calls == before_calls
    assert restored.inventory().query("ean == @ean")["comparable"].all()
    assert len(store.dashboard(now=clock())[0].query("ean == @ean")) == 1


def test_only_selected_candidate_is_confirmed_and_duplicate_submit_is_rejected(tmp_path):
    store, locator, _, _, cid, _, rows, urls = _review_env(tmp_path, count=2)
    ean = rows[0][0]
    row = _candidate(locator, ean)
    expected = _expected(row)

    def submit():
        return locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Selección explícita",
                              update_price=True, expected_candidate=expected)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _submit_result(submit), range(2)))
    assert sum(isinstance(value, int) for value in results) == 1
    assert sum(isinstance(value, ValueError) for value in results) == 1
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes").fetchone()[0] == 1
        assert con.execute("SELECT status FROM competitor_product_urls WHERE ean=? AND competitor_id=?",
                           (rows[0][0], cid)).fetchone()[0] == "verified"
        assert con.execute("SELECT status FROM competitor_product_urls WHERE ean=? AND competitor_id=?",
                           (rows[1][0], cid)).fetchone()[0] == "probable"
        assert con.execute("SELECT state FROM locate_jobs WHERE ean=? AND competitor_id=?",
                           (rows[1][0], cid)).fetchone()[0] == "review"


def _submit_result(submit):
    try:
        return submit()
    except ValueError as exc:
        return exc


def test_stale_preview_is_excluded_without_acceptance_or_http(tmp_path):
    store, locator, _, _, cid, site, rows, urls = _review_env(tmp_path)
    row = _candidate(locator, rows[0][0])
    expected = _expected(row)
    with store._connect() as con, con:
        con.execute("UPDATE competitor_product_urls SET color_code='OTHER' WHERE competitor_id=? AND ean=?",
                    (cid, rows[0][0]))
    with pytest.raises(ValueError, match="cambió"):
        locator.review(rows[0][0], cid, urls[0], accept=True, actor="ana", note="Vista previa obsoleta",
                       update_price=True, expected_candidate=expected)
    with store._connect() as con:
        assert con.execute("SELECT status FROM competitor_product_urls WHERE ean=? AND competitor_id=?",
                           (rows[0][0], cid)).fetchone()[0] == "probable"
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes").fetchone()[0] == 0
    assert site.calls == []


def test_robots_and_out_of_stock_preserve_reason_and_do_not_create_comparable_offer(tmp_path):
    store, locator, monitor, _, cid, site, rows, urls = _review_env(
        tmp_path, robots="User-agent: *\nDisallow: /gafas/\n")
    ean = rows[0][0]
    row = _candidate(locator, ean)
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Revisión",
                          update_price=True, expected_candidate=_expected(row))
    result = monitor.review_refresh_tick()
    assert result["processed"] == 0 and result["deferred"] == 1
    assert site.calls == [f"{site.base}/robots.txt"]
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert progress["status"] == "deferred" and progress["retry_at"]
    assert progress["attempts"] == 0 and progress["http_requests"] == 1
    assert "robots" in progress["detail"].lower()
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0

    # A separate eligible route with no stock records the observation but not a comparable offer.
    out_of_stock = tmp_path / "out-of-stock"
    out_of_stock.mkdir()
    store2, locator2, monitor2, _, cid2, site2, rows2, urls2 = _review_env(out_of_stock, stock=False)
    row2 = _candidate(locator2, rows2[0][0])
    task2 = locator2.review(rows2[0][0], cid2, urls2[0], accept=True, actor="ana", note="Revisión",
                             update_price=True, expected_candidate=_expected(row2))
    assert monitor2.review_refresh_tick()["processed"] == 1
    progress2 = monitor2.review_refreshes().set_index("id").loc[task2]
    assert progress2["status"] == "done" and not progress2["eligible"]
    assert "no comparable" in progress2["detail"].lower()
    assert not monitor2.inventory()["comparable"].any()


def test_immediate_read_does_not_bypass_a_future_monitor_restriction(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    older = (clock() - timedelta(hours=2)).isoformat()
    retry = (clock() + timedelta(minutes=5)).isoformat()
    with store._connect() as con, con:
        con.execute("""INSERT INTO monitor_jobs(
            ean,competitor_id,url,state,not_before,attempts,last_ok_at,sampled_at,valid_until,updated_at)
            VALUES(?,?,?,'done',?,0,?,?,?,?)""",
                    (ean, cid, urls[0], retry, older, older, (clock() + timedelta(hours=24)).isoformat(),
                     clock().isoformat()))
    row = _candidate(locator, ean)
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Respeta el aplazamiento",
                          update_price=True, expected_candidate=_expected(row))
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert progress["status"] == "deferred" and progress["retry_at"] == retry
    assert monitor.review_refresh_tick()["processed"] == 0
    assert site.calls == []


def test_access_budget_blocks_read_and_reference_identity_keeps_stock_unknown(tmp_path):
    store, locator, monitor, _, cid, site, rows, urls = _review_env(tmp_path)
    AccessControl(store).set_budget("competitor", cid, daily_limit=0, per_minute_limit=600,
                                    pause_seconds=0, actor="ana")
    row = _candidate(locator, rows[0][0])
    task = locator.review(rows[0][0], cid, urls[0], accept=True, actor="ana", note="Lectura gobernada",
                          update_price=True, expected_candidate=_expected(row))
    result = monitor.review_refresh_tick()
    assert result["processed"] == 0 and result["deferred"] == 1
    progress = monitor.review_refreshes().set_index("id").loc[task]
    assert progress["status"] == "deferred" and "presupuesto" in progress["detail"].lower()
    assert site.calls == []
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0

    conflict = tmp_path / "conflicting-ean"
    conflict.mkdir()
    store2, locator2, monitor2, _, cid2, _, rows2, urls2 = _review_env(
        conflict, gtin_override=_gtin("841999999999"))
    row2 = _candidate(locator2, rows2[0][0])
    task2 = locator2.review(rows2[0][0], cid2, urls2[0], accept=True, actor="ana",
                            note="Revalidar identidad", update_price=True,
                            expected_candidate=_expected(row2))
    assert monitor2.review_refresh_tick()["processed"] == 1
    progress2 = monitor2.review_refreshes().set_index("id").loc[task2]
    assert progress2["status"] == "done"
    with store2._connect() as con:
        offer = con.execute("SELECT ean_published,availability,match_basis FROM offers WHERE ean=?",
                            (rows2[0][0],)).fetchone()
        eligible = con.execute("SELECT eligible FROM monitor_jobs WHERE ean=? AND competitor_id=?",
                               (rows2[0][0], cid2)).fetchone()[0]
        assert con.execute("SELECT status FROM competitor_product_urls WHERE ean=? AND competitor_id=?",
                           (rows2[0][0], cid2)).fetchone()[0] == "verified"
    assert offer["ean_published"] == _gtin("841999999999")
    assert offer["availability"] == "unknown" and offer["match_basis"] == "reference"
    assert eligible == 0
    summary, _ = store2.dashboard()
    assert summary.loc[summary["ean"].eq(rows2[0][0]), "competitor_count"].item() == 0


def test_review_tray_excludes_advanced_locator_controls(tmp_path, monkeypatch):
    store, _, _, _, cid, _, _, _ = _review_env(tmp_path, count=2)
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    names = {row["id"]: row["name"] for row in store.competitors().to_dict("records")}
    app = AppTest.from_string(
        "from app_pages.price_watch import _render_locator\n"
        "from price_comparator import PriceComparatorStore\n"
        f"_render_locator(PriceComparatorStore(), {names!r}, advanced=False)\n"
    ).run(timeout=60)
    assert not app.exception
    assert not any(button.key == "locate_enqueue" for button in app.button)
    assert not any(button.key == "locate_run" for button in app.button)
    assert not any("Preparar cola" in button.label for button in app.button)


def test_bulk_discard_revalidates_candidate_and_never_requests_http(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path, count=2)
    first, second = rows
    first_row = _candidate(locator, first[0])
    second_row = _candidate(locator, second[0])
    before_offers = 0
    with store._connect() as con:
        before_offers = con.execute("SELECT COUNT(*) FROM offers").fetchone()[0]

    locator.review(first[0], cid, urls[0], accept=False, actor="ana",
                   note="Descartar conjunto sintético", update_price=False,
                   expected_candidate=_expected(first_row))
    clock.advance(microseconds=1)
    locator.review(second[0], cid, urls[1], accept=False, actor="ana",
                   note="Descartar conjunto sintético", update_price=False,
                   expected_candidate=_expected(second_row))

    assert site.calls == []
    assert monitor.review_refreshes().empty
    with store._connect() as con:
        statuses = con.execute("""SELECT status FROM competitor_product_urls
            WHERE competitor_id=? AND ean IN (?,?)""",
                               (cid, first[0], second[0])).fetchall()
        assert {row["status"] for row in statuses} == {"rejected"}
        assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == before_offers
        audit_count = con.execute("""SELECT COUNT(*) FROM access_audit
            WHERE action='candidate_rejected'""").fetchone()
    assert audit_count is not None and audit_count[0] == 2


def test_existing_monitor_deferral_keeps_its_specific_reason(tmp_path):
    store, locator, monitor, clock, cid, site, rows, urls = _review_env(tmp_path)
    ean = rows[0][0]
    task = locator.review(ean, cid, urls[0], accept=True, actor="ana", note="Lectura gobernada",
                          update_price=True, expected_candidate=_expected(_candidate(locator, ean)))
    with store._connect() as con, con:
        con.execute("""UPDATE monitor_jobs SET state='waiting',detail='Presupuesto diario agotado',not_before=?
            WHERE competitor_id=? AND ean=?""", ((clock() + timedelta(hours=1)).isoformat(), cid, ean))
    assert monitor.review_refresh_tick()["deferred"] == 1
    with store._connect() as con:
        row = con.execute("SELECT detail,retry_at FROM manual_review_refreshes WHERE id=?", (task,)).fetchone()
    assert "Presupuesto diario agotado" in row["detail"]
    assert row["retry_at"]
    assert site.calls == []
