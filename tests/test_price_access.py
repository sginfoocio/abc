from datetime import datetime, timedelta, timezone
import threading

import pandas as pd
import pytest

from price_access import AccessControl, AccessDenied, GovernedFetcher
from price_connectors import ConnectorBlocked
from tests.test_price_comparator import _store

ROBOTS = b"User-agent: *\nAllow: /\n"
PAGE = b"<html><body><h1>Gafas</h1></body></html>"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Site:
    def __init__(self, responses=None):
        self.responses, self.calls = responses or {}, []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return 200, {}, ROBOTS
        return self.responses.get(url, (200, {"Content-Type": "text/html"}, PAGE))


@pytest.fixture()
def env(tmp_path):
    store = _store(tmp_path)
    store.seed_defaults()
    clock = Clock()
    access = AccessControl(store, clock=clock)
    cid = store.competitors().set_index("name").loc["Tienda EUR", "id"]
    with store._connect() as connection, connection:
        connection.execute("INSERT OR REPLACE INTO competitor_web_config(competitor_id,pause_seconds) VALUES(?,0)",
                           (cid,))
    return store, access, clock, cid


def _authorize(access, cid, *methods):
    for method in methods:
        access.set_method(cid, method, "authorized", actor="ana", note="Acuerdo por escrito")


def _requests(store):
    with store._connect() as connection:
        return pd.read_sql_query("SELECT * FROM access_requests ORDER BY id", connection)


def test_nothing_is_authorized_by_default_and_known_search_prohibitions_are_recorded(env):
    store, access, _, cid = env
    assert access.ensure_defaults() > 0
    assert access.ensure_defaults() == 0  # idempotent
    names = store.competitors().set_index("name")["id"]
    domanci = names["Domanci"]
    assert access.method_status(domanci, "site_search") == "denied"
    assert access.method_status(domanci, "ficha") == "pending"
    assert access.method_status(domanci, "external") == "pending"
    grid = access.methods()
    assert not (grid["status"] == "authorized").any()
    assert set(grid["method"]) == {"ficha", "site_search", "sitemap", "catalog", "feed", "external"}
    assert access.check(cid, "ficha").code == "not_authorized"
    assert access.check(domanci, "site_search").code == "prohibited"


def test_authorizations_are_per_method_justified_and_audited(env):
    store, access, _, cid = env
    with pytest.raises(ValueError):
        access.set_method(cid, "ficha", "authorized", actor="ana", note=" ")
    with pytest.raises(ValueError):
        access.set_method(cid, "ficha", "authorized", actor="", note="ok")
    with pytest.raises(ValueError):
        access.set_method(cid, "scraping", "authorized", actor="ana", note="ok")
    access.set_method(cid, "ficha", "authorized", actor="ana", note="Condiciones revisadas", evidence="https://x")
    assert access.check(cid, "ficha").allowed
    assert access.check(cid, "site_search").code == "not_authorized"
    access.set_method(cid, "ficha", "denied", actor="luis", note="Retirada")
    assert access.check(cid, "ficha").code == "prohibited"
    audit = access.audit()
    assert list(audit["current"]) == ["denied", "authorized"]
    assert list(audit["actor"]) == ["luis", "ana"]


def test_budgets_are_configurable_without_code_and_enforced(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    assert access.budget("competitor", cid)["daily_limit"] == 200 and not access.budget("competitor", cid)["custom"]
    with pytest.raises(ValueError):
        access.set_budget("competitor", cid, daily_limit=10, per_minute_limit=0, pause_seconds=None, actor="ana")
    with pytest.raises(ValueError):
        access.set_budget("competitor", "no-existe", daily_limit=10, per_minute_limit=1, pause_seconds=None,
                          actor="ana")
    access.set_budget("competitor", cid, daily_limit=3, per_minute_limit=2, pause_seconds=None, actor="ana")
    access.reserve(cid, "ficha", purpose="locate")
    access.reserve(cid, "ficha", purpose="locate")
    minute = access.check(cid, "ficha")
    assert minute.code == "rate_minute" and minute.waitable
    clock.advance(seconds=61)
    access.reserve(cid, "ficha", purpose="monitor")
    with pytest.raises(AccessDenied) as denied:
        access.reserve(cid, "ficha", purpose="monitor")
    assert denied.value.decision.code == "budget_daily"
    assert denied.value.decision.retry_at == datetime(2026, 11, 4, 8, tzinfo=timezone.utc)
    assert len(_requests(store)) == 3  # a refusal is never recorded as a request
    clock.advance(hours=24)
    assert access.check(cid, "ficha").allowed  # rolling 24 h window
    assert access.audit()["action"].iloc[0] == "budget"


def test_pause_uses_the_stricter_of_budget_and_competitor_web_pause(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    with store._connect() as connection, connection:
        connection.execute("UPDATE competitor_web_config SET pause_seconds=5 WHERE competitor_id=?", (cid,))
    access.set_budget("competitor", cid, daily_limit=50, per_minute_limit=50, pause_seconds=2, actor="ana")
    assert access.budget("competitor", cid)["effective_pause"] == 5
    access.reserve(cid, "ficha", purpose="locate")
    clock.advance(seconds=3)
    decision = access.check(cid, "ficha")
    assert decision.code == "pause" and decision.retry_at == clock.now + timedelta(seconds=2)
    clock.advance(seconds=2)
    assert access.check(cid, "ficha").allowed


def test_external_search_is_disabled_by_default_even_if_authorized(env, monkeypatch):
    store, access, _, cid = env
    monkeypatch.delenv("PRICE_EXTERNAL_SEARCH_ENABLED", raising=False)
    _authorize(access, cid, "external", "catalog")
    assert access.check(cid, "external", provider="any").code == "external_disabled"
    with pytest.raises(AccessDenied):
        access.reserve(cid, "external", purpose="locate", provider="any")
    assert access.check(cid, "catalog").allowed  # own catalogue navigation is an independent method
    assert _requests(store).empty


def test_optional_external_search_spends_the_provider_budget_not_the_competitor_one(env, monkeypatch):
    store, access, clock, cid = env
    monkeypatch.setenv("PRICE_EXTERNAL_SEARCH_ENABLED", "1")
    with pytest.raises(ValueError):
        access.check(cid, "external")
    assert access.check(cid, "external", provider="futuro").code == "not_authorized"
    _authorize(access, cid, "external")
    assert access.budget("provider", "futuro")["daily_limit"] == 100
    access.set_budget("provider", "futuro", daily_limit=2, per_minute_limit=10, pause_seconds=None, actor="ana")
    access.reserve(cid, "external", purpose="locate", provider="futuro")
    access.reserve(cid, "external", purpose="locate", provider="futuro")
    assert access.check(cid, "external", provider="futuro").code == "budget_daily"
    assert access.usage("competitor", cid) == 0 and access.usage("provider", "futuro") == 2
    budgets = access.budgets()
    assert set(budgets["scope"]) == {"competitor", "provider"}
    assert budgets.set_index("scope_id").loc["futuro", "used_24h"] == 2


def test_http_429_waits_for_retry_after_and_resumes_alone(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    access.report_block("competitor", cid, "http_429", url="https://eur.example/p",
                        retry_after=clock.now + timedelta(minutes=30))
    assert access.report_block("competitor", cid, "http_429") == access.incidents()["id"].iloc[0]  # no duplicates
    decision = access.check(cid, "ficha")
    assert decision.code == "blocked_wait" and decision.retry_at == clock.now + timedelta(minutes=30)
    clock.advance(minutes=31)
    access.reserve(cid, "ficha", purpose="monitor")
    assert access.incidents()["status"].iloc[0] == "expired"


def test_captcha_or_403_needs_a_documented_human_decision(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    incident = access.report_block("competitor", cid, "http_403", detail="HTTP 403",
                                   retry_after=clock.now + timedelta(seconds=1))
    clock.advance(days=30)
    assert access.check(cid, "ficha").code == "blocked_manual"  # 403 never resumes on its own
    with pytest.raises(ValueError):
        access.resolve_incident(incident, actor="ana", note="")
    access.resolve_incident(incident, actor="ana", note="IP de Vytria autorizada por el competidor")
    assert access.check(cid, "ficha").allowed
    with pytest.raises(ValueError):
        access.resolve_incident(incident, actor="ana", note="otra vez")
    assert access.audit()["action"].iloc[0] == "incident_resolved"


def test_concurrent_reservations_never_exceed_the_daily_budget(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    access.set_budget("competitor", cid, daily_limit=5, per_minute_limit=600, pause_seconds=None, actor="ana")
    granted, denied = [], []

    def worker():
        for _ in range(4):
            try:
                granted.append(AccessControl(store, clock=clock).reserve(cid, "ficha", purpose="locate"))
            except AccessDenied:
                denied.append(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 5 and len(denied) == 11
    assert len(_requests(store)) == 5


def _fetcher(access, cid, site, clock, method="ficha"):
    def sleep(seconds):
        clock.advance(seconds=seconds)

    return GovernedFetcher(access, cid, method, purpose="locate", ean="0036000291452", transport=site,
                           sleep=sleep, clock=lambda: clock.now.timestamp(), pause_seconds=0, max_requests=10)


def test_governed_fetcher_reserves_every_request_including_robots(env):
    store, access, clock, cid = env
    site = Site()
    with pytest.raises(AccessDenied):
        _fetcher(access, cid, site, clock).get("https://eur.example/p")
    assert site.calls == []  # an unauthorised method performs no HTTP at all
    _authorize(access, cid, "ficha")
    access.set_budget("competitor", cid, daily_limit=10, per_minute_limit=10, pause_seconds=4, actor="ana")
    fetcher = _fetcher(access, cid, site, clock)
    assert fetcher.get("https://eur.example/p").status == 200
    fetcher.get("https://eur.example/q")
    log = _requests(store)
    assert list(log["url"]) == ["https://eur.example/robots.txt", "https://eur.example/p", "https://eur.example/q"]
    assert set(log["result"]) == {"HTTP 200"} and set(log["purpose"]) == {"locate"}
    stamps = pd.to_datetime(log["reserved_at"])
    assert (stamps.diff().dropna() >= pd.Timedelta(seconds=4)).all()  # the cross-process pause was honoured


def test_governed_fetcher_suspends_on_429_and_does_not_retry(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    site = Site({"https://eur.example/p": (429, {"Retry-After": "120"}, b"")})
    with pytest.raises(ConnectorBlocked):
        _fetcher(access, cid, site, clock).get("https://eur.example/p")
    incident = access.incidents().iloc[0]
    assert incident["kind"] == "http_429" and incident["status"] == "open"
    assert pd.Timestamp(incident["retry_after"]) == pd.Timestamp(clock.now + timedelta(seconds=120))
    with pytest.raises(AccessDenied):
        _fetcher(access, cid, Site(), clock).get("https://eur.example/other")
    assert site.calls.count("https://eur.example/p") == 1


def test_governed_fetcher_records_captcha_and_never_bypasses_it(env):
    store, access, clock, cid = env
    _authorize(access, cid, "ficha")
    site = Site({"https://eur.example/p": (200, {}, b"<html>Please complete the CAPTCHA</html>")})
    with pytest.raises(ConnectorBlocked):
        _fetcher(access, cid, site, clock).get("https://eur.example/p")
    assert access.incidents()["kind"].iloc[0] == "captcha"
    clock.advance(days=2)
    assert access.check(cid, "ficha").code == "blocked_manual"
