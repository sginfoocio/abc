"""Durable manual-review confirmation and immediate price reads; all HTTP is synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest
from streamlit.testing.v1 import AppTest

from app_pages.price_watch import _review_expected_candidate
from price_monitor import PriceMonitor
from price_access import AccessControl
from tests.test_price_locator import ROBOTS, Site, _env, _gtin, _watch


def _review_env(tmp_path, *, count=1, stock=True, gtin_override=None, robots=ROBOTS):
    site = Site(robots=robots)
    store, _, locator, clock, cid = _env(tmp_path, site)
    with store._connect() as con, con:
        con.execute("UPDATE competitors SET active=(id=?)", (cid,))
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
        refresh = con.execute("SELECT actor,reason,status FROM manual_review_refreshes").fetchone()
        assert refresh["actor"] == "admin" and refresh["reason"] == "Validación manual"
        assert refresh["status"] == "pending"
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
        task = con.execute("SELECT status,http_requests,detail FROM manual_review_refreshes WHERE id=?",
                           (task_id,)).fetchone()
        job = con.execute("SELECT state,eligible,sample FROM monitor_jobs WHERE competitor_id=? AND ean=?",
                          (cid, ean)).fetchone()
        assert task["status"] == "done" and task["http_requests"] > 0
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


def test_access_budget_and_identity_contradiction_never_create_comparable_offers(tmp_path):
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
    assert progress2["status"] == "error" and "EAN" in progress2["detail"]
    with store2._connect() as con:
        assert con.execute("SELECT status FROM competitor_product_urls WHERE ean=? AND competitor_id=?",
                           (rows2[0][0], cid2)).fetchone()[0] == "probable"
        assert con.execute("SELECT COUNT(*) FROM offers WHERE ean=?", (rows2[0][0],)).fetchone()[0] == 0
