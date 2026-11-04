"""Pilot boundary tests use 100 temporary products and simulated HTTP only."""
import json

import pandas as pd
import pytest

from price_comparator import PriceComparatorStore, normalize_gtin
from price_pilot import (
    COMPETITOR_NAMES, HTTP_BUDGET_EXHAUSTED, REQUIRED_EANS, SAMPLE_EANS, PilotError, PricePilot, pilot_scope,
)
from price_scheduler import CompetitorScheduler, SchedulerConfigError
from price_web import WebPriceSearch, WebSearchError
from tests.test_price_review_fixes import _freeze, _vytria
from tests.web_fixtures import BASE, NOW, PRADA, FakeSite, competitor_id, index_url, prada_page, url_of


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    monkeypatch.setattr("price_pilot.stamp", lambda: NOW.isoformat(timespec="seconds"))
    store = PriceComparatorStore(tmp_path / "pilot.sqlite3")
    extras = []
    for index in range(80):
        base = f"200000{index:06d}"
        checksum = (10 - sum(int(v) * (1 if i % 2 == 0 else 3) for i, v in enumerate(base)) % 10) % 10
        extras.append(normalize_gtin(base + str(checksum)))
    products = []
    for index, ean in enumerate((*SAMPLE_EANS, *extras)):
        data = PRADA.get(ean, {"model": f"TEST {index}", "color": "001", "caliber": "54-21"})
        products.append({"EAN": ean, "MARCA": "Prada", "MODELO": data["model"], "COLOR": data["color"],
                         "CALIBRE": data["caliber"]})
    assert not store.import_products(pd.DataFrame(products)).rejected
    store.seed_defaults()
    for ean in (*SAMPLE_EANS, *extras):
        _vytria(store, ean, 300)
    service = PricePilot(store)
    ids = {r["id"]: {"pause_seconds": 1, "max_http_requests": 10} for r in service.competitors()}
    site = FakeSite()
    search = WebPriceSearch(store, fetcher_factory=site.factory)
    cid = competitor_id(store)
    search.ensure_config()
    with store._connect() as c, c:
        c.execute("""UPDATE competitor_web_config SET access_status='robots_allowed',terms_reviewed=1,
            terms_note='Autorización para prueba offline',locate_strategy='site_search' WHERE competitor_id=?""", (cid,))
        c.execute("UPDATE competitors SET market='ES',currency='EUR' WHERE id=?", (cid,))
    service.save(SAMPLE_EANS, ids)
    return service, store, search, site, cid, extras


def known(pilot, ean=REQUIRED_EANS[0], *, verified=True, **overrides):
    service, store, search, site, cid, _ = pilot
    url = url_of(PRADA[ean]["slug"])
    index_url(store, cid, ean, url)
    if verified:
        with store._connect() as connection, connection:
            connection.execute("""UPDATE competitor_product_urls SET status='verified'
                WHERE competitor_id=? AND ean=? AND url=?""", (cid, ean, url))
    site.add(url, prada_page(ean, **overrides))
    return url


def _insert_budget_error(service, store, cid, ean, *, blocked=0):
    scope = pilot_scope(store)
    with store._connect() as connection, connection:
        run_id = connection.execute("""INSERT INTO pilot_runs(
            started_at,status,scope,competitors,operation,selection,selection_request)
            VALUES('2026-11-02T10:00:00+00:00','done',?,?,'discovery',?,?)""",
                                    (json.dumps(scope["eans"]), json.dumps(scope["competitors"]),
                                     json.dumps([[cid, ean]]), json.dumps([[cid, ean]]))).lastrowid
        connection.execute("INSERT INTO pilot_budgets(run_id,competitor_id,requests,blocked) VALUES(?,?,10,?)",
                           (run_id, cid, blocked))
        job_id = connection.execute("""INSERT INTO pilot_jobs(
            run_id,competitor_id,ean,status,outcome,detail,checked_at)
            VALUES(?,?,?,'done','error',?,'2026-11-02T10:05:00+00:00')""",
                                    (run_id, cid, ean, HTTP_BUDGET_EXHAUSTED)).lastrowid
        connection.execute("""INSERT INTO pilot_discovery(competitor_id,ean,run_id,state,detail,updated_at)
            VALUES(?,?,?,'error',?,'2026-11-02T10:05:00+00:00')
            ON CONFLICT(competitor_id,ean) DO UPDATE SET run_id=excluded.run_id,state=excluded.state,
                detail=excluded.detail,updated_at=excluded.updated_at""",
                           (cid, ean, run_id, HTTP_BUDGET_EXHAUSTED))
    return int(job_id), int(run_id)


def test_scope_persisted_exact_and_initially_disabled(pilot):
    service, store, search, site, cid, extras = pilot
    scope = pilot_scope(PriceComparatorStore(store.database_path))
    assert set(scope["eans"]) == set(SAMPLE_EANS)
    assert len(scope["competitors"]) == 6
    assert {r["name"] for r in service.competitors()} == set(COMPETITOR_NAMES)
    assert not scope["enabled"]
    assert service.candidates()["eligible"].sum() == 100
    assert len(service.plan()) == 120
    assert set(service.plan()["ean"]).isdisjoint(extras)
    assert site.calls == []
    with pytest.raises(PilotError):
        service.run(search=search)
    with store._connect() as c:
        assert c.execute("SELECT SUM(enabled) FROM competitor_schedule").fetchone()[0] == 0
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_pilot_limit_update_preserves_enabled_scope_and_pending_history(pilot):
        service, store, search, site, cid, _ = pilot
        service.enable()
        scope = pilot_scope(store)
        with store._connect() as connection, connection:
            run_id = connection.execute("""INSERT INTO pilot_runs(
                started_at,status,scope,competitors,operation,selection,selection_request)
                VALUES('2026-11-02T10:00:00+00:00','interrupted',?,?,'discovery','[]','[]')""",
                                        (json.dumps(scope["eans"]), json.dumps(scope["competitors"]))).lastrowid
            connection.execute("INSERT INTO pilot_budgets(run_id,competitor_id) VALUES(?,?)", (run_id, cid))
            connection.execute("INSERT INTO pilot_jobs(run_id,competitor_id,ean) VALUES(?,?,?)",
                               (run_id, cid, REQUIRED_EANS[0]))
        before_jobs = service.plan().copy()
        limits = scope["competitors"]
        limits[cid]["max_http_requests"] = 100
        limits[cid]["pause_seconds"] = 3

        service.update_limits(limits)

        updated = pilot_scope(store)
        assert updated["enabled"] == 1 and updated["eans"] == scope["eans"]
        assert updated["competitors"] == limits
        with store._connect() as connection:
            assert connection.execute("SELECT status FROM pilot_runs WHERE id=?", (run_id,)).fetchone()[0] == "interrupted"
            assert connection.execute("SELECT status FROM pilot_jobs WHERE run_id=?", (run_id,)).fetchone()[0] == "pending"
        assert service.plan().equals(before_jobs.assign(
            max_http_requests=before_jobs["max_http_requests"].where(
                before_jobs["competitor_id"] != cid, 100),
            pause_seconds=before_jobs["pause_seconds"].where(
                before_jobs["competitor_id"] != cid, 3)))
        assert site.calls == []


def test_budget_recovery_prepares_exact_selection_once_without_http(pilot):
        service, store, search, site, cid, _ = pilot
        service.enable()
        first, source_run = _insert_budget_error(service, store, cid, REQUIRED_EANS[0])
        second, _ = _insert_budget_error(service, store, cid, REQUIRED_EANS[1])

        visible = service.budget_recovery_jobs()
        assert set(visible.loc[visible["Recuperable"], "job_id"]) == {first, second}
        before_source = visible.set_index("job_id").loc[[first, second], ["source_run_id", "Estado", "Motivo"]].copy()
        result = service.recover_budget_errors([first, second], operator="Operadora offline",
                                               reason="Presupuesto aumentado y autorización revisada")

        assert result == {"prepared_jobs": 2, "references": 2, "run_id": result["run_id"]}
        with store._connect() as connection:
            staged = connection.execute("SELECT * FROM pilot_runs WHERE id=?", (result["run_id"],)).fetchone()
            staged_jobs = connection.execute("""SELECT competitor_id,ean,status FROM pilot_jobs
                WHERE run_id=? ORDER BY ean""", (result["run_id"],)).fetchall()
            budget = connection.execute("SELECT requests,blocked FROM pilot_budgets WHERE run_id=?",
                                        (result["run_id"],)).fetchone()
            audits = connection.execute("""SELECT source_job_id,operator,reason,prepared_run_id
                FROM pilot_recovery_audit ORDER BY source_job_id""").fetchall()
            sources = connection.execute("SELECT id,run_id,status,outcome,detail FROM pilot_jobs WHERE id IN (?,?)",
                                         (first, second)).fetchall()
        assert staged["status"] == "interrupted" and staged["operation"] == "discovery"
        assert "requiere ejecución supervisada" in staged["error"]
        assert [tuple(row) for row in staged_jobs] == [
            (cid, REQUIRED_EANS[0], "pending"), (cid, REQUIRED_EANS[1], "pending")]
        assert tuple(budget) == (0, 0)
        assert [row["source_job_id"] for row in audits] == [first, second]
        assert all(row["operator"] == "Operadora offline" and row["prepared_run_id"] == result["run_id"]
                   for row in audits)
        assert all(row["reason"] == "Presupuesto aumentado y autorización revisada" for row in audits)
        assert [(row["id"], row["run_id"], row["status"], row["outcome"], row["detail"])
                for row in sources] == [(first, source_run, "done", "error", HTTP_BUDGET_EXHAUSTED),
                                        (second, sources[1]["run_id"], "done", "error", HTTP_BUDGET_EXHAUSTED)]
        assert service.budget_recovery_jobs()["prepared_run_id"].notna().all()
        second_attempt = service.recover_budget_errors([first, second], operator="Operadora offline",
                                                       reason="Repetición idempotente")
        assert second_attempt == {"prepared_jobs": 0, "references": 0, "run_id": None}
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pilot_runs").fetchone()[0] == 3
            assert connection.execute("SELECT COUNT(*) FROM pilot_recovery_audit").fetchone()[0] == 2
        assert not site.calls
        pd.testing.assert_frame_equal(
            service.budget_recovery_jobs().set_index("job_id").loc[[first, second],
                                                                   ["source_run_id", "Estado", "Motivo"]],
            before_source)


def test_budget_recovery_excludes_blocked_unauthorized_and_candidate_jobs(pilot):
        service, store, _, site, cid, _ = pilot
        service.enable()
        eligible, _ = _insert_budget_error(service, store, cid, REQUIRED_EANS[0])
        blocked, _ = _insert_budget_error(service, store, cid, REQUIRED_EANS[1], blocked=1)
        other_cid = next(value for value in pilot_scope(store)["competitors"] if value != cid)
        unauthorized, _ = _insert_budget_error(service, store, other_cid, REQUIRED_EANS[2])
        candidate, run_id = _insert_budget_error(service, store, cid, SAMPLE_EANS[3])
        with store._connect() as connection, connection:
            connection.execute("""INSERT INTO pilot_url_candidates(
                competitor_id,ean,url,run_id,method,evidence,identity,checked_at,state)
                VALUES(?,?,?,?,'site_search','candidate evidence','candidate identity','2026-11-02','candidate_found')""",
                               (cid, SAMPLE_EANS[3], BASE + "/candidate", run_id))
        jobs = service.budget_recovery_jobs().set_index("job_id")
        assert jobs.loc[eligible, "Recuperable"]
        assert "bloqueo" in jobs.loc[blocked, "recovery_note"]
        assert "autorización" in jobs.loc[unauthorized, "recovery_note"]
        assert "candidato" in jobs.loc[candidate, "recovery_note"]
        with pytest.raises(PilotError, match="No se pueden recuperar"):
            service.recover_budget_errors([blocked, unauthorized, candidate], operator="Ruben", reason="Revisado")
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pilot_recovery_audit").fetchone()[0] == 0
        assert site.calls == []


def test_budget_recovery_requires_operator_reason_and_pilot_lock(pilot):
        service, store, _, site, cid, _ = pilot
        job_id, _ = _insert_budget_error(service, store, cid, REQUIRED_EANS[0])
        with pytest.raises(PilotError, match="operador"):
            service.recover_budget_errors([job_id], operator=" ", reason="Revisado")
        with service.lock(), pytest.raises(PilotError, match="consulta en curso"):
            service.recover_budget_errors([job_id], operator="Ruben", reason="Revisado")
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM pilot_recovery_audit").fetchone()[0] == 0
        assert site.calls == []


def test_direct_manual_forced_and_scheduler_boundaries(pilot):
    service, store, search, site, cid, extras = pilot
    service.enable()
    other = next(r["id"] for r in store.competitors().to_dict("records") if r["name"] not in COMPETITOR_NAMES)
    for ean, competitor in [(extras[0], cid), (SAMPLE_EANS[0], other), (SAMPLE_EANS[0], cid)]:
        with pytest.raises(WebSearchError):
            search.locate(ean, competitor)
        with pytest.raises(WebSearchError):
            search.run([ean], [competitor])
        with pytest.raises(WebSearchError):
            search.submit_url(ean, competitor, BASE + "/product")
    assert search.check_access(cid)["access_status"] == "robots_allowed"
    assert site.calls == [BASE + "/robots.txt"]
    site.calls.clear()
    scheduler = CompetitorScheduler(store)
    assert scheduler.run_once(force=True) == []
    with pytest.raises(SchedulerConfigError):
        scheduler.run_once(competitor_ids=[other], force=True, trigger="manual")
    with pytest.raises(SchedulerConfigError):
        scheduler.run_once(workers=2, trigger="manual")
    with pytest.raises(SchedulerConfigError):
        scheduler._start_cycle(None, {}, {}, NOW)
    with pytest.raises(SchedulerConfigError):
        scheduler.verify(cid)
    assert site.calls == []


def test_no_urls_means_no_discovery_no_network_and_no_outside_jobs(pilot):
    service, store, search, site, cid, extras = pilot
    service.enable()
    run = service.run(search=search)
    with store._connect() as c:
        jobs = c.execute("SELECT * FROM pilot_jobs WHERE run_id=?", (run,)).fetchall()
    assert len(jobs) == 120
    assert {r["ean"] for r in jobs} == set(SAMPLE_EANS)
    assert not set(extras) & {r["ean"] for r in jobs}
    assert len({r["competitor_id"] for r in jobs}) == 6
    assert {r["outcome"] for r in jobs} == {"not_found", "not_allowed"}
    assert site.calls == []
    frame, metrics = service.results()
    assert metrics["Ofertas comparables"] == 0
    assert len(frame) == 120


def test_price_verification_dedup_and_history_survives_error(pilot):
    service, store, search, site, cid, extras = pilot
    url = known(pilot)
    service.enable()
    service.run(search=search)
    frame, metrics = service.results()
    row = frame.loc[(frame.ean == REQUIRED_EANS[0]) & (frame.competitor_id == cid)].iloc[0]
    assert row.comparable == 1, row.detail
    assert row["Diferencia frente a Vytria €"] == -61
    assert metrics["Precios extraídos"] == 1
    service.run(search=search)
    with store._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
        assert c.execute("SELECT checks FROM offer_checks").fetchone()[0] == 1
    site.add(url, status=500)
    service.run(search=search)
    with store._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1


def test_selected_product_refresh_uses_only_confirmed_authorized_urls(pilot):
    service, store, search, site, cid, _ = pilot
    url = known(pilot)
    with store._connect() as connection, connection:
        connection.execute("""UPDATE competitor_product_urls SET status='verified',source='user'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, REQUIRED_EANS[0], url))

    plan = service.price_refresh_plan(REQUIRED_EANS[0]).set_index("competitor_id")
    assert plan.loc[cid, "status"] == "Consultable"
    assert plan.loc[cid, "url"] == url
    assert site.calls == []

    service.enable()
    run_id = service.run(search=search, selection=[(cid, REQUIRED_EANS[0])])
    with store._connect() as connection:
        jobs = connection.execute(
            "SELECT competitor_id,ean FROM pilot_jobs WHERE run_id=?", (run_id,)).fetchall()
        budgets = connection.execute(
            "SELECT competitor_id FROM pilot_budgets WHERE run_id=?", (run_id,)).fetchall()
    assert [tuple(row) for row in jobs] == [(cid, REQUIRED_EANS[0])]
    assert [row["competitor_id"] for row in budgets] == [cid]
    assert site.calls == [BASE + "/robots.txt", url]

    service.run(search=search, selection=[(cid, REQUIRED_EANS[0])])
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
        assert connection.execute("SELECT checks FROM offer_checks").fetchone()[0] == 1

    previous_price = float(store.offer_history(REQUIRED_EANS[0]).iloc[0]["price"])
    updated_price = previous_price - 10
    site.add(url, prada_page(REQUIRED_EANS[0], price=updated_price))
    service.run(search=search, selection=[(cid, REQUIRED_EANS[0])])
    history = store.offer_history(REQUIRED_EANS[0])
    assert len(history) == 2
    assert set(history["price"]) == {previous_price, updated_price}


def test_selected_product_refresh_never_uses_unconfirmed_urls(pilot):
    service, store, search, site, cid, _ = pilot
    url = known(pilot, verified=False)
    service.enable()

    run_id = service.run(search=search, selection=[(cid, REQUIRED_EANS[0])])

    assert site.calls == []
    with store._connect() as connection:
        job = connection.execute(
            "SELECT status,outcome,detail FROM pilot_jobs WHERE run_id=?", (run_id,)).fetchone()
    assert job["status"] == "done"
    assert job["outcome"] == "not_found"
    assert "No hay ficha confirmada" in job["detail"]
    assert search._indexed(cid, REQUIRED_EANS[0])[0]["url"] == url


def test_product_detail_starts_only_explicitly_selected_price_refresh(pilot, monkeypatch):
    import app_pages.price_comparator as page
    from streamlit.testing.v1 import AppTest

    _, store, search, site, cid, _ = pilot
    url = known(pilot)
    with store._connect() as connection, connection:
        connection.execute("""UPDATE competitor_product_urls SET status='verified',source='user'
            WHERE competitor_id=? AND ean=? AND url=?""", (cid, REQUIRED_EANS[0], url))
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    monkeypatch.setattr(page, "WebPriceSearch", lambda _: search)
    launched = []
    monkeypatch.setattr(page, "_launch", lambda target, *args: launched.append((target, args)))

    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=60)
    assert not app.exception and site.calls == []
    app.selectbox(key="dashboard_product").set_value(REQUIRED_EANS[0]).run(timeout=60)
    app.checkbox(key=f"dashboard_refresh_approved_{REQUIRED_EANS[0]}").set_value(True).run(timeout=60)
    app.button(key=f"dashboard_refresh_prices_{REQUIRED_EANS[0]}").click().run(timeout=60)

    assert not app.exception and site.calls == []
    assert len(launched) == 1
    assert launched[0][0] is page._run_price_refresh
    assert launched[0][1][1] == [(cid, REQUIRED_EANS[0])]


@pytest.mark.parametrize("override,reason", [
    ({"stock": False}, "stock"),
    ({"pretax": None}, "IVA"),
    ({"market": None}, "Mercado"),
    ({"currency": "USD"}, "Moneda"),
    ({"name": "Prada PR 27ZS ZVN09T 54-21"}, "Color"),
    ({"brand": "Gucci"}, "Marca"),
])
def test_noncomparable_observations_visible(pilot, override, reason):
    service, store, search, site, cid, extras = pilot
    known(pilot, **override)
    service.enable()
    service.run(search=search)
    frame, metrics = service.results()
    row = frame.loc[(frame.ean == REQUIRED_EANS[0]) & (frame.competitor_id == cid)].iloc[0]
    assert row.comparable == 0
    assert reason.lower() in row.detail.lower(), row.detail
    assert json.loads(row.observation)["page"]
    assert store.dashboard()[1]["confirmed"] == 0


def test_reference_match_without_published_ean_remains_comparable_in_pilot(pilot):
    service, store, search, site, cid, _ = pilot
    known(pilot, gtin=None)
    service.enable()
    service.run(search=search)
    frame, _ = service.results()
    row = frame.loc[(frame.ean == REQUIRED_EANS[0]) & (frame.competitor_id == cid)].iloc[0]
    validation = json.loads(row.observation)["validation"]
    assert row.comparable == 1
    assert validation["verified_ean"] is False
    assert validation["listing"]["colors"] == ["16K08Z"]


@pytest.mark.parametrize("status,body", [(403, ""), (429, ""), (200, "<html>captcha</html>")])
def test_block_no_retry_in_run_or_resume(pilot, status, body):
    service, store, search, site, cid, extras = pilot
    first = known(pilot)
    known(pilot, REQUIRED_EANS[1])
    site.add(first, body, status)
    service.enable()
    service.run(search=search)
    assert site.calls.count(first) == 1
    assert len(site.calls) == 2
    with store._connect() as c:
        assert c.execute("SELECT blocked FROM pilot_budgets WHERE competitor_id=?", (cid,)).fetchone()[0] == 1


def test_budget_persisted_resume_and_disable_mid_http(pilot):
    service, store, search, site, cid, extras = pilot
    known(pilot)
    known(pilot, REQUIRED_EANS[1])
    limits = pilot_scope(store)["competitors"]
    limits[cid]["max_http_requests"] = 2
    service.save(SAMPLE_EANS, limits)
    service.enable()
    original = site.transport

    def stop_after_request(*args):
        result = original(*args)
        service.disable()
        return result

    site.transport = stop_after_request
    with pytest.raises(PilotError):
        service.run(search=search)
    assert len(site.calls) == 1  # robots only; disable prevents the product request
    with store._connect() as c:
        run = c.execute("SELECT id FROM pilot_runs").fetchone()[0]
        assert c.execute("SELECT requests FROM pilot_budgets WHERE competitor_id=?", (cid,)).fetchone()[0] == 1
    site.transport = original
    service.enable()
    assert service.run(resume=True, search=search) == run
    assert len(site.calls) == 2  # remaining request covers robots; no budget refunded after crash


def test_empty_invalid_scope_and_legacy_queue_quarantined(pilot):
    service, store, search, site, cid, extras = pilot
    with pytest.raises(PilotError):
        service.save([], pilot_scope(store)["competitors"])
    with store._connect() as c, c:
        c.execute("""INSERT INTO scheduler_jobs(competitor_id,ean,cycle_started_at,status,not_before,created_at)
            VALUES(?,?,'2026-10-31','pending','2026-10-31','2026-10-31')""", (cid, extras[0]))
    service.save(SAMPLE_EANS, pilot_scope(store)["competitors"])
    with store._connect() as c:
        assert c.execute("SELECT status FROM scheduler_jobs").fetchone()[0] == "error"
    with store._connect() as c, c:
        c.execute("UPDATE price_pilot SET eans='[]'")
    with pytest.raises(PilotError):
        service.enable()
    with pytest.raises(WebSearchError):
        search.locate(extras[0], cid)
    assert site.calls == []


def test_ui_scope_plan_and_manual_confirmation(pilot, monkeypatch):
    from streamlit.testing.v1 import AppTest
    service, store, search, site, cid, extras = pilot
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    from tests.admin_navigation import navigate
    navigate(app, "Piloto y consultas", timeout=30)
    assert not app.exception
    assert app.selectbox(key="price_pilot_section").value == "Configuración del piloto"
    assert len(app.multiselect(key="pilot_eans").value) == 20
    assert len(app.multiselect(key="pilot_competitors").value) == 6
    assert app.button(key="pilot_save_limits") is not None
    scope_before = pilot_scope(store)
    app.button(key="pilot_save_limits").click().run(timeout=30)
    assert not app.exception
    assert pilot_scope(store)["enabled"] == scope_before["enabled"]
    assert site.calls == []


@pytest.mark.parametrize("field,value", [("eans", '[[]]'), ("eans", '["invalid"]'),
                                        ("competitors", '{}')])
def test_corrupt_scope_denies_before_http(pilot, field, value):
    service, store, search, site, cid, extras = pilot
    with store._connect() as c, c:
        c.execute(f"UPDATE price_pilot SET {field}=?", (value,))
    with pytest.raises(PilotError):
        service.run(search=search)
    with pytest.raises(WebSearchError):
        search.locate(REQUIRED_EANS[0], cid)
    assert site.calls == []


def test_other_competitor_configs_unchanged_and_save_race_fails(pilot):
    from filelock import FileLock
    service, store, search, site, cid, extras = pilot
    other = next(r["id"] for r in store.competitors().to_dict("records") if r["name"] not in COMPETITOR_NAMES)
    with store._connect() as c:
        before = tuple(c.execute("SELECT * FROM competitor_schedule WHERE competitor_id=?", (other,)).fetchone())
    with FileLock(f"{store.database_path}.competitor-{other}.lock", timeout=0):
        with pytest.raises(PilotError):
            service.save(SAMPLE_EANS, pilot_scope(store)["competitors"])
    with store._connect() as c:
        after = tuple(c.execute("SELECT * FROM competitor_schedule WHERE competitor_id=?", (other,)).fetchone())
    assert after == before
    assert site.calls == []


def test_all_six_competitors_with_authorized_known_pages(pilot, monkeypatch):
    service, store, search, site, cid, extras = pilot
    from tests.web_fixtures import ROBOTS_OK
    from urllib.parse import urlsplit
    ean = REQUIRED_EANS[0]
    pages = []
    for row in service.competitors():
        config = search.config(row["id"])
        base = config["site_url"].rstrip("/")
        url = base + "/pilot-product.html"
        pages.append(url)
        origin = urlsplit(base)
        site.add(f"{origin.scheme}://{origin.netloc}/robots.txt", ROBOTS_OK)
        site.add(url, prada_page(ean))
        index_url(store, row["id"], ean, url)
        search.update_config(row["id"], terms_reviewed=True, terms_note="Permiso simulado para pruebas",
                             access_status="robots_allowed")
        search.confirm_market(row["id"], "ES", "EUR")
    service.enable()
    monkeypatch.setattr("price_web.WebPriceSearch", lambda store: search)
    summaries = CompetitorScheduler(store).run_once(force=True, trigger="manual")
    assert len(summaries) == 1 and summaries[0].status == "ok"
    assert set(site.calls) == set(pages) | {
        f"{urlsplit(page).scheme}://{urlsplit(page).netloc}/robots.txt" for page in pages}
    assert len(site.calls) == 12
    assert service.results()[1]["Competidores consultados"] == 6
    with store._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM pilot_jobs").fetchone()[0] == 120
        assert c.execute("SELECT COUNT(*) FROM scheduler_jobs").fetchone()[0] == 0


def test_missing_catalog_caliber_is_informational_for_verified_ean(pilot):
    service, store, search, site, cid, extras = pilot
    known(pilot)
    with store._connect() as c, c:
        c.execute("UPDATE products SET caliber='' WHERE ean=?", (REQUIRED_EANS[0],))
    service.enable()
    service.run(search=search)
    frame, metrics = service.results()
    row = frame.loc[(frame.ean == REQUIRED_EANS[0]) & (frame.competitor_id == cid)].iloc[0]
    assert row.comparable == 1
    assert json.loads(row.observation)["validation"]["caliber_informational"]
    assert metrics["Ofertas comparables"] == 1
    assert store.dashboard()[1]["confirmed"] == 1


def test_limits_revalidated_on_loading_persistent_scope(pilot):
    service, store, search, site, cid, extras = pilot
    settings = pilot_scope(store)["competitors"]
    settings[cid]["max_http_requests"] = 0
    with store._connect() as c, c:
        c.execute("UPDATE price_pilot SET competitors=?", (json.dumps(settings),))
    with pytest.raises(PilotError, match="Presupuesto"):
        service.run(search=search)
    assert site.calls == []


def test_injected_old_pilot_jobs_cannot_escape_scope_on_resume(pilot):
    service, store, search, site, cid, extras = pilot
    known(pilot)
    original = site.transport

    def interrupt(*args):
        original(*args)
        raise KeyboardInterrupt("Simulated process termination")

    site.transport = interrupt
    service.enable()
    with pytest.raises(KeyboardInterrupt):
        service.run(search=search)
    with store._connect() as c, c:
        run_id = c.execute("SELECT id FROM pilot_runs").fetchone()[0]
        c.execute("INSERT INTO pilot_jobs(run_id,competitor_id,ean) VALUES(?,?,?)", (run_id, cid, extras[0]))
    site.transport = original
    assert service.run(resume=True, search=search) == run_id
    with store._connect() as c:
        job = c.execute("SELECT status,outcome FROM pilot_jobs WHERE ean=?", (extras[0],)).fetchone()
        assert tuple(job) == ("cancelled", "pilot_excluded")
    assert all("sitemap" not in url and "search" not in url for url in site.calls)


def test_pilot_worker_errors_are_visible_not_swallowed(pilot, monkeypatch):
    from app_pages.price_comparator import _run_pilot
    service, store, search, site, cid, extras = pilot
    _run_pilot(service, False)
    assert "preparado" in pilot_scope(store)["last_error"]
    assert site.calls == []


def test_ui_launches_only_approved_discovery_in_background(pilot, monkeypatch):
    import app_pages.price_comparator as page
    from streamlit.testing.v1 import AppTest
    service, store, search, site, cid, extras = pilot
    with store._connect() as connection, connection:
        connection.execute("UPDATE competitor_web_config SET search_mode='site_search',search_verified=1 "
                           "WHERE competitor_id=?", (cid,))
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    calls = []
    monkeypatch.setattr(page, "_launch", lambda target, *args: calls.append((target, args)))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    from tests.admin_navigation import navigate
    navigate(app, "Piloto y consultas", timeout=30)
    app.selectbox(key="price_pilot_section").set_value("Localización supervisada").run(timeout=30)
    app.selectbox(key="discovery_execution_competitor").set_value(cid).run(timeout=30)
    selected_ean = app.selectbox(key="discovery_execution_ean").value
    start = app.button(key="discovery_start")
    assert start.disabled
    approval = next(item for item in app.checkbox
                    if item.label == "He revisado el alcance seleccionado, los métodos y las autorizaciones")
    approval.check().run(timeout=30)
    assert not app.button(key="discovery_start").disabled, (
        f"warnings={[item.value for item in app.warning]}, "
        f"info={[item.value for item in app.info]}, "
        f"markdown={[item.value for item in app.markdown]}"
    )
    app.button(key="discovery_start").click().run(timeout=30)
    assert not app.exception
    assert len(calls) == 1 and calls[0][0] == page._run_discovery
    assert calls[0][1][0].store.database_path == store.database_path
    assert calls[0][1][1:] == (False, [(cid, selected_ean)])
    assert pilot_scope(store)["enabled"]
    assert site.calls == []


def test_ui_shows_unauthorized_competitor_reasons_without_http(pilot, monkeypatch):
    from streamlit.testing.v1 import AppTest
    service, store, search, site, cid, _ = pilot
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    with store._connect() as connection, connection:
        connection.execute("UPDATE competitor_web_config SET access_status='pending_review',terms_reviewed=0 "
                           "WHERE competitor_id=?", (cid,))
    name = next(row["name"] for row in service.competitors() if row["id"] == cid)
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    from tests.admin_navigation import navigate
    navigate(app, "Piloto y consultas", timeout=30)
    app.selectbox(key="price_pilot_section").set_value("Localización supervisada").run(timeout=30)
    app.selectbox(key="discovery_execution_competitor").set_value(cid).run(timeout=30)
    assert not app.exception
    assert any(name in warning.value and "autoriz" in warning.value.lower() for warning in app.warning)
    assert app.button(key="discovery_start").disabled
    assert site.calls == []


def test_ui_prepares_budget_recovery_with_audit_and_no_http(pilot, monkeypatch):
    from streamlit.testing.v1 import AppTest
    service, store, search, site, cid, _ = pilot
    service.enable()
    job_id, _ = _insert_budget_error(service, store, cid, REQUIRED_EANS[0])
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))
    app = AppTest.from_string("from app_pages.price_comparator import render_price_comparator_page\n"
                              "render_price_comparator_page()").run(timeout=30)
    from tests.admin_navigation import navigate
    navigate(app, "Recuperación de búsquedas", timeout=30)
    assert not app.exception
    assert any("no hace consultas HTTP" in item.value for item in app.caption)
    app.multiselect(key="price_recovery_job_ids").set_value([job_id]).run(timeout=30)
    app.text_input(key="price_recovery_operator").set_value("Revisión offline").run(timeout=30)
    app.text_input(key="price_recovery_reason").set_value("Autorización confirmada y presupuesto revisado").run(
        timeout=30)
    app.checkbox(key="price_recovery_confirmation").check().run(timeout=30)
    assert not app.button(key="price_recovery_prepare").disabled
    app.button(key="price_recovery_prepare").click().run(timeout=30)
    assert not app.exception
    assert any("Se han preparado 1 referencias" in item.value for item in app.success)
    with store._connect() as connection:
        audit = connection.execute(
            "SELECT operator,reason FROM pilot_recovery_audit WHERE source_job_id=?", (job_id,)).fetchone()
    assert tuple(audit) == ("Revisión offline", "Autorización confirmada y presupuesto revisado")
    assert site.calls == []


def test_access_revocation_stops_next_http_request(pilot):
    service, store, search, site, cid, extras = pilot
    known(pilot)
    original = site.transport

    def revoke(*args):
        result = original(*args)
        search.update_config(cid, terms_reviewed=False)
        return result

    site.transport = revoke
    service.enable()
    with pytest.raises(PilotError, match="retirada"):
        service.run(search=search)
    assert site.calls == [BASE + "/robots.txt"]
