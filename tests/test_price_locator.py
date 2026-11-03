"""Phase 2: self-hosted automatic locator (queue, incremental discovery index, auto-confirmation, recovery).

Every site here is simulated: no real network is used.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from price_access import AccessControl
from price_locator import LEASE, UNAVAILABLE_TEXT, PriceLocator, link_matches, signature
from price_watch import PriceWatch
from tests.test_price_comparator import _store
from tests.test_price_watch import _gtin
from tests.web_fixtures import page

BASE = "https://eur.example"
ROBOTS = "User-agent: *\nDisallow: /carrito\n"
# Domanci-like rules: internal search and filter parameters are forbidden; brand pages and fichas are not.
DOMANCI_ROBOTS = ("User-agent: *\nDisallow: /*es/busqueda\nDisallow: /*?q=\nDisallow: /*?n=\nDisallow: /*?order=\n"
                  "Disallow: /*?search_query=\n")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Crash(BaseException):
    """Simulates the process being killed in the middle of a run."""


class Site:
    """A competitor catalogue: brand listings with pagination and one product page per model and colour."""

    def __init__(self, base=BASE, robots=ROBOTS, per_page=25):
        self.base, self.robots, self.per_page = base, robots, per_page
        self.pages: dict[str, str] = {}
        self.brands: dict[str, list[tuple[str, str]]] = {}
        self.status: dict[str, int] = {}
        self.calls: list[str] = []
        self.crash_after: int | None = None

    def product(self, brand, model, color, *, caliber="54-18", gtin=None, stock=True, html=None, slug=None):
        slug = slug or "-".join([*brand.lower().split(), *model.lower().split(), color.lower(), caliber]) + ".html"
        url = f"{self.base}/gafas/{slug}"
        self.pages[url] = html or page(name=f"{brand} {model} {color} {caliber}", price=120, brand=brand,
                                       gtin=gtin, stock=stock)
        self.brands.setdefault("-".join(brand.lower().split()), []).append((url, f"{brand} {model} {color}"))
        return url

    def listing(self, brand_slug, number):
        links = self.brands.get(brand_slug, [])
        chunk = links[(number - 1) * self.per_page:number * self.per_page]
        body = "".join(f'<a href="{url}">{text}</a>' for url, text in chunk)
        body += '<a href="/gafas-de-sol">Gafas de sol</a><a href="/carrito">Carrito</a>'
        if number * self.per_page < len(links):
            body += f'<a href="?page={number + 1}">Siguiente</a>'
        return f"<html><body>{body}</body></html>"

    def __call__(self, url, headers, timeout):
        if self.crash_after is not None and len(self.calls) >= self.crash_after:
            raise Crash()
        self.calls.append(url)
        if url in self.status:
            return self.status[url], {"Retry-After": "120"}, b"blocked"
        if url.endswith("/robots.txt"):
            return 200, {}, self.robots.encode()
        path, _, query = url.removeprefix(self.base).partition("?")
        for prefix in ("/marca/", "/es/marca/"):
            if path.startswith(prefix) and path[len(prefix):] in self.brands:
                number = int(query.removeprefix("page=") or 1) if query.startswith("page=") or not query else 0
                if number:
                    return 200, {"Content-Type": "text/html"}, self.listing(path[len(prefix):], number).encode()
        if url in self.pages:
            return 200, {"Content-Type": "text/html"}, self.pages[url].encode()
        return 404, {}, b""

    def count(self, fragment):
        return sum(fragment in call for call in self.calls)


def _env(tmp_path, site, *, methods=("ficha", "catalog"), name="Tienda EUR", template=BASE + "/marca/{brand}"):
    store = _store(tmp_path)
    store.seed_defaults()
    clock = Clock()
    access = AccessControl(store, clock=clock)
    access.ensure_defaults()
    cid = store.competitors().set_index("name").loc[name, "id"]
    with store._connect() as connection, connection:
        connection.execute("INSERT OR IGNORE INTO competitor_web_config(competitor_id) VALUES(?)", (cid,))
        connection.execute("UPDATE competitor_web_config SET pause_seconds=0 WHERE competitor_id=?", (cid,))
    for method in methods:
        access.set_method(cid, method, "authorized", actor="ana", note="Acuerdo por escrito con el competidor")
    access.set_budget("competitor", cid, daily_limit=100_000, per_minute_limit=600, pause_seconds=None, actor="ana")
    locator = PriceLocator(store, transport=site, clock=clock, sleep=lambda seconds: clock.advance(seconds=seconds))
    if template:
        locator.save_source(cid, "catalog", template, actor="ana", note="Páginas de marca")
    return store, access, locator, clock, cid


def _watch(store, rows, clock=None):
    frame = pd.DataFrame([{"MARCA": brand, "NOMBRE": f"{brand} {model}", "MODELO": model, "COLOR": color,
                           "CATEGORIA": "Sol", "EAN": ean, **({"CALIBRE": caliber} if caliber else {})}
                          for ean, brand, model, color, caliber in rows])
    report = store.import_products(frame)
    assert not report.rejected, report.rejected
    watched = PriceWatch(store).add([row[0] for row in rows], actor="ana", source="import")
    assert not watched.rejected
    return watched


def _jobs(store, cid):
    with store._connect() as connection:
        return pd.read_sql_query("SELECT * FROM locate_jobs WHERE competitor_id=? ORDER BY id", connection,
                                 params=(cid,))


def _urls(store, cid):
    with store._connect() as connection:
        return pd.read_sql_query("SELECT * FROM competitor_product_urls WHERE competitor_id=?", connection,
                                 params=(cid,))


BRANDS = [("Alpha", "AL"), ("Beta Optic", "BO"), ("Gamma", "GM"), ("Delta Eyes", "DE"), ("Epsilon", "EP")]
COLORS = ["001", "002", "10A", "20B", "93E"]


def _catalogue_500(site):
    rows = []
    for b, (brand, prefix) in enumerate(BRANDS):
        for m in range(20):
            model = f"{prefix} {1000 + m}" if m % 2 else f"{prefix}{1000 + m}"
            for c, color in enumerate(COLORS):
                ean = _gtin(f"84{b}{m:03d}{c:02d}0000"[:12])
                rows.append((ean, brand, model, color, ""))
                # Every third page publishes its EAN; the others publish none. Calibres vary freely.
                site.product(brand, model, color, caliber=f"{50 + c}-18", gtin=ean if (m + c) % 3 == 0 else None,
                             stock=(m + c) % 4 != 0)
            site.product(brand, model, "99Z")  # colour sold by the competitor but not watched
    return rows


def test_500_watched_eans_are_discovered_confirmed_and_recovered_after_interruption(tmp_path):
    site = Site()
    store, access, locator, clock, cid = _env(tmp_path, site)
    rows = _catalogue_500(site)
    assert len({row[0] for row in rows}) == 500
    watched = _watch(store, rows)  # adding to the watch list queues the location jobs by itself
    assert watched.queued == 500 * len(store.competitors())  # every watched product × every active competitor
    initial = _jobs(store, cid)
    assert len(initial) == 500 and initial["url"].eq("").all() and set(initial["state"]) == {"pending"}
    assert locator.enqueue()["created"] == 0  # idempotent: no duplicate jobs

    site.crash_after = 260  # the process dies in the middle of the run
    with pytest.raises(Crash):
        locator.run([cid], max_jobs=10_000)
    jobs = _jobs(store, cid)
    assert (jobs["state"] == "running").sum() == 1  # leased job left behind by the crash
    done_before = int((jobs["state"] == "done").sum())
    assert 200 < done_before < 500
    first_pass_listings = {call for call in site.calls if "/marca/" in call}

    site.crash_after = None
    clock.advance(seconds=LEASE.total_seconds() + 1)
    report = locator.run([cid], max_jobs=10_000)
    jobs = _jobs(store, cid)
    assert Counter(jobs["state"]) == Counter({"done": 500})
    assert report.states["done"] == 500 - done_before
    assert jobs.loc[jobs["attempts"] > 0].empty  # settled jobs reset their attempts

    urls = _urls(store, cid)
    verified = urls[urls["status"] == "verified"]
    assert len(verified) == 500 and verified["ean"].nunique() == 500
    assert set(verified["source"]) == {"auto_watch"}
    assert verified["note"].str.contains("regla marca \\+ modelo \\+ código de color").all()
    assert not verified["url"].str.contains("-99z-").any()
    by_ean = verified.set_index("ean")["url"]
    for ean, brand, model, color, _ in rows[:25]:
        assert f"-{color.lower()}-" in by_ean[ean] and "-".join(model.lower().split()) in by_ean[ean]

    # Incremental: each listing page fetched once overall; already indexed pages are not fetched again.
    listing_calls = Counter(call for call in site.calls if "/marca/" in call)
    assert max(listing_calls.values()) == 1
    assert len(listing_calls) == 5 * 5  # 5 brands × ceil(105 links / 25 per page)
    assert first_pass_listings <= set(listing_calls)
    product_calls = Counter(call for call in site.calls if "/gafas/" in call)
    assert sum(count > 1 for count in product_calls.values()) <= 1  # only the page being read at the crash
    assert not any("-99z-" in call for call in product_calls)  # targeted: unwatched colours never fetched

    with store._connect() as connection:
        reserved = connection.execute("SELECT COUNT(*) FROM access_requests WHERE competitor_id=?",
                                      (cid,)).fetchone()[0]
        indexed = connection.execute("SELECT COUNT(*) FROM discovery_index WHERE competitor_id=?",
                                     (cid,)).fetchone()[0]
    # Every HTTP request went through the access gate; the one interrupted by the crash was reserved but never sent.
    assert reserved == len(site.calls) + 1
    assert indexed == 5 * 20 * 6

    # A new run reuses verified URLs: no further HTTP.
    calls = len(site.calls)
    locator.enqueue()
    assert locator.run([cid], max_jobs=10_000).processed == 0
    assert len(site.calls) == calls


def test_index_is_reused_between_products_and_runs_without_new_requests(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site)
    first = site.product("Alpha", "AL1001", "10A", caliber="52-18")
    rows = [(_gtin("840000000001"), "Alpha", "AL1001", "10A", "52-18"),
            (_gtin("840000000002"), "Alpha", "AL1001", "10A", "56-18")]  # same model + colour, other calibre
    _watch(store, rows[:1])
    locator.enqueue()
    locator.run([cid])
    calls = len(site.calls)
    _watch(store, rows[1:])
    locator.enqueue()
    report = locator.run([cid])
    assert report.states["done"] == 1 and report.requests == 0 and len(site.calls) == calls
    verified = _urls(store, cid).set_index("ean")
    assert verified.loc[rows[1][0], "url"] == first and verified.loc[rows[1][0], "status"] == "verified"

    # Listings are only refreshed after they expire (refresh_hours of the source, 168 h by default).
    third = (_gtin("840000000003"), "Alpha", "AL1002", "002", "")
    site.product("Alpha", "AL1002", "002")
    _watch(store, [third])
    locator.enqueue()
    clock.advance(hours=1)
    assert locator.run([cid]).states["not_found"] == 1  # the new page is not in the fresh copy of the listing
    clock.advance(days=8)
    assert locator.run([cid]).states["done"] == 1


@pytest.mark.parametrize("case,html_kwargs,expected_state,url_status", [
    ("sin EAN publicado", {}, "done", "verified"),
    ("EAN publicado idéntico", {"gtin": "self"}, "done", "verified"),
    ("calibre diferente", {"name": "Alpha AL1001 10A 60-14"}, "done", "verified"),
    ("sin stock", {"stock": False}, "done", "verified"),
    ("EAN contradictorio", {"gtin": _gtin("840000009999")}, "review", "probable"),
    ("color incorrecto", {"name": "Alpha AL1001 20B 54-18"}, "not_found", "rejected"),
    ("modelo incorrecto", {"name": "Alpha AL1009 10A 54-18"}, "not_found", "rejected"),
])
def test_identity_rule_brand_model_colour(tmp_path, case, html_kwargs, expected_state, url_status):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    ean = _gtin("840000000001")
    args = {"name": "Alpha AL1001 10A 54-18", "price": 99, "brand": "Alpha", **html_kwargs}
    if args.get("gtin") == "self":
        args["gtin"] = ean
    site.product("Alpha", "AL1001", "10A", html=page(**args))
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "54-18")])
    locator.enqueue()
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == expected_state, (case, job["detail"])
    assert _urls(store, cid).iloc[0]["status"] == url_status


def test_ambiguous_variants_and_missing_colour_code_are_not_confirmed(tmp_path):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    two = page(name="Alpha AL1001 10A 54-18", price=99, brand="Alpha").replace("</head>", "") + \
        page(name="Alpha AL1001 20B 54-18", price=99, brand="Alpha")
    site.product("Alpha", "AL1001", "10A", html=two)
    no_colour = page(name="Alpha AL1002", price=99, brand="Alpha")
    site.product("Alpha", "AL1002", "002", html=no_colour)
    _watch(store, [(_gtin("840000000001"), "Alpha", "AL1001", "10A", ""),
                   (_gtin("840000000002"), "Alpha", "AL1002", "002", "")])
    locator.enqueue()
    locator.run([cid])
    states = _jobs(store, cid).set_index("ean")["state"]
    assert set(states) == {"review"}
    assert set(_urls(store, cid)["status"]) == {"probable"}
    assert _urls(store, cid)["status"].ne("verified").all()


def test_previously_discarded_or_rejected_urls_are_never_auto_confirmed(tmp_path):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    ean = _gtin("840000000001")
    url = site.product("Alpha", "AL1001", "10A")
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "")])
    with store._connect() as connection, connection:
        connection.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,match_status,status,verified_at,
            source,note) VALUES(?,?,?,'rejected','rejected','2026-10-01','user','Descartada por Ana')""",
                           (cid, ean, url))
    locator.enqueue()
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "not_found"
    assert _urls(store, cid).iloc[0]["status"] == "rejected"
    assert url not in site.calls


def test_review_decisions_are_audited(tmp_path):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    ean = _gtin("840000000001")
    contradictory = page(name="Alpha AL1001 10A 54-18", price=99, brand="Alpha", gtin=_gtin("840000009999"))
    url = site.product("Alpha", "AL1001", "10A", html=contradictory)
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "")])
    locator.enqueue()
    locator.run([cid])
    with pytest.raises(ValueError):
        locator.review(ean, cid, url, accept=True, actor="ana", note=" ")
    locator.review(ean, cid, url, accept=True, actor="ana", note="EAN de otra talla; mismo modelo y color")
    row = _urls(store, cid).iloc[0]
    assert (row["status"], row["source"]) == ("verified", "user")
    assert _jobs(store, cid).iloc[0]["state"] == "done"
    with store._connect() as connection:
        events = [r["state"] for r in connection.execute("SELECT state FROM locate_events ORDER BY id")]
    assert events == ["review", "done"]


def test_without_authorised_methods_nothing_is_requested_and_nothing_is_called_missing(tmp_path):
    site = Site()
    store, access, locator, _, cid = _env(tmp_path, site, methods=())
    _watch(store, [(_gtin("840000000001"), "Alpha", "AL1001", "10A", "")])
    locator.enqueue()
    report = locator.run()
    assert site.calls == [] and report.requests == 0
    jobs = locator.jobs()
    assert set(jobs["state"]) == {"unavailable"}
    assert jobs["detail"].str.startswith(UNAVAILABLE_TEXT).all()
    assert not jobs["state"].eq("not_found").any()

    # Fichas authorised but no discovery method: still a restriction, still no request.
    access.set_method(cid, "ficha", "authorized", actor="ana", note="Acuerdo")
    access.set_method(cid, "catalog", "denied", actor="ana", note="Condiciones de uso")
    locator.enqueue()  # reopens «unavailable» jobs after an authorisation change
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == "unavailable" and "sin autorizar" in job["detail"] and site.calls == []


def test_external_search_stays_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICE_EXTERNAL_SEARCH_ENABLED", raising=False)
    site = Site()
    calls = []
    store, access, _, clock, cid = _env(tmp_path, site, methods=("ficha", "external"), template=None)
    locator = PriceLocator(store, transport=site, clock=clock, sleep=lambda s: None,
                           external_search=lambda product, config: calls.append(product) or [])
    _watch(store, [(_gtin("840000000001"), "Alpha", "AL1001", "10A", "")])
    locator.enqueue()
    locator.run([cid])
    assert calls == [] and site.calls == []
    assert _jobs(store, cid).iloc[0]["state"] == "unavailable"


@pytest.mark.parametrize("status,state,incident_wait", [(429, "waiting", True), (403, "blocked", False)])
def test_blocks_suspend_the_competitor_without_retrying(tmp_path, status, state, incident_wait):
    site = Site()
    store, access, locator, clock, cid = _env(tmp_path, site)
    rows = [(_gtin(f"84000000000{i}"), "Alpha", f"AL100{i}", "10A", "") for i in range(1, 4)]
    for _, brand, model, color, _ in rows:
        site.product(brand, model, color)
    site.status[BASE + "/marca/alpha"] = status
    _watch(store, rows)
    locator.enqueue()
    report = locator.run([cid])
    assert cid in report.stopped
    assert site.count("/marca/alpha") == 1  # never insisted
    jobs = _jobs(store, cid)
    assert set(jobs["state"]) == {state}
    assert jobs["attempts"].eq(0).all()  # suspension is not a failed attempt
    incidents = access.incidents(open_only=True)
    assert len(incidents) == 1
    assert pd.notna(incidents.iloc[0]["retry_after"]) == incident_wait

    calls = len(site.calls)
    clock.advance(minutes=1)
    locator.run([cid])
    assert len(site.calls) == calls  # still suspended: no request at all
    if incident_wait:
        del site.status[BASE + "/marca/alpha"]
        clock.advance(hours=2)
        locator.run([cid])
        assert set(_jobs(store, cid)["state"]) == {"done"}


def test_captcha_blocks_and_needs_a_human(tmp_path):
    site = Site()
    store, access, locator, _, cid = _env(tmp_path, site)
    captcha = "<html><body>Please complete the CAPTCHA (g-recaptcha)</body></html>"
    url = site.product("Alpha", "AL1001", "10A", html=captcha)
    _watch(store, [(_gtin("840000000001"), "Alpha", "AL1001", "10A", "")])
    locator.enqueue()
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "blocked"
    assert access.incidents(open_only=True).iloc[0]["kind"] == "captcha"
    assert site.count(url) == 1


def test_exhausted_budget_puts_jobs_on_hold(tmp_path):
    site = Site()
    store, access, locator, _, cid = _env(tmp_path, site)
    access.set_budget("competitor", cid, daily_limit=3, per_minute_limit=600, pause_seconds=None, actor="ana")
    rows = [(_gtin(f"84000000000{i}"), "Alpha", f"AL100{i}", "10A", "") for i in range(1, 6)]
    for _, brand, model, color, _ in rows:
        site.product(brand, model, color)
    _watch(store, rows)
    locator.enqueue()
    report = locator.run([cid])
    assert len(site.calls) == 3  # robots.txt + listing + one ficha
    jobs = _jobs(store, cid)
    assert (jobs["state"] == "done").sum() == 1
    assert set(jobs.loc[jobs["state"] != "done", "state"]) == {"waiting"}
    assert jobs.loc[jobs["state"] == "waiting", "not_before"].notna().all()
    assert "Presupuesto diario" in report.stopped[cid]


def test_supervised_pilot_pairs_are_skipped(tmp_path, monkeypatch):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    ean = _gtin("840000000001")
    site.product("Alpha", "AL1001", "10A")
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "")])
    monkeypatch.setattr("price_pilot.pilot_scope", lambda store: {"eans": [ean], "competitors": {cid: {}}})
    locator.enqueue()
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "pilot" and site.calls == []


def test_dead_verified_page_is_relocated(tmp_path):
    site = Site()
    store, _, locator, clock, cid = _env(tmp_path, site)
    ean = _gtin("840000000001")
    old = f"{BASE}/gafas/antigua-al1001.html"
    new = site.product("Alpha", "AL1001", "10A")
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "")])
    with store._connect() as connection, connection:
        connection.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,match_status,status,
            verified_at) VALUES(?,?,?,'exact_match','verified','2026-10-01')""", (cid, ean, old))
    locator.enqueue()
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "done" and site.calls == []  # reused, no request
    with store._connect() as connection, connection:  # the monitoring phase found it gone (HTTP 404)
        connection.execute("UPDATE competitor_product_urls SET status='needs_relocation' WHERE url=?", (old,))
    assert locator.enqueue()["relocate"] == 1
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert (job["kind"], job["state"], job["url"]) == ("relocate", "done", new)
    statuses = _urls(store, cid).set_index("url")["status"]
    assert statuses[old] == "replaced" and statuses[new] == "verified"


def test_domanci_like_site_uses_brand_pages_only_when_authorised(tmp_path):
    site = Site(base="https://domanci.com", robots=DOMANCI_ROBOTS)
    store, access, locator, clock, _ = _env(tmp_path, site, methods=(), template=None)
    cid = store.competitors().set_index("name").loc["Domanci", "id"]
    with store._connect() as connection, connection:
        connection.execute("INSERT OR IGNORE INTO competitor_web_config(competitor_id) VALUES(?)", (cid,))
        connection.execute("UPDATE competitor_web_config SET pause_seconds=0 WHERE competitor_id=?", (cid,))
    assert locator.seed_sources() == 1 and locator.seed_sources() == 0
    rows = []
    for i, color in enumerate(["16K08Z", "1AB5S0"]):
        url = site.product("Prada", "PR 27ZS", color, slug=f"es/gafas-de-sol/prada-pr-27zs-{color.lower()}-54.html")
        site.pages[url.replace("/gafas/es/", "/es/")] = site.pages.pop(url)
        rows.append((_gtin(f"80565978753{i}"), "Prada", "PR 27ZS", color, ""))
    site.brands["prada"] = [(url.replace("/gafas/es/", "/es/"), text) for url, text in site.brands["prada"]]
    _watch(store, rows)
    locator.enqueue()

    # Documented situation today: search forbidden by robots.txt, fichas and catalogue pending authorisation.
    assert access.method_status(cid, "site_search") == "denied"
    locator.run([cid])
    assert set(_jobs(store, cid)["state"]) == {"unavailable"} and site.calls == []

    # Simulated future authorisation of catalogue + fichas (not enabled in production).
    access.set_method(cid, "ficha", "authorized", actor="ana", note="Simulación de autorización")
    access.set_method(cid, "catalog", "authorized", actor="ana", note="Simulación de autorización")
    access.set_budget("competitor", cid, daily_limit=1000, per_minute_limit=600, pause_seconds=None, actor="ana")
    locator.enqueue()
    locator.run([cid])
    assert set(_jobs(store, cid)["state"]) == {"done"}
    assert not any("busqueda" in call or "?q=" in call for call in site.calls)
    assert site.count("/es/marca/prada") == 1


def test_link_prefilter_needs_model_and_colour_as_separate_tokens():
    sig = signature("https://x.es/es/gafas-de-sol/prada-pr-27zs-16k08z-54.html")
    assert link_matches(sig, "PR 27ZS", "16K08Z")
    assert link_matches(signature("https://x.es/prada-0pr-27zs-16k08z"), "PR 27ZS", "16K08Z")
    assert link_matches(signature("https://x.es/a", "Tom Ford Bronson FT1044S 93E"), "FT1044", "93E")
    assert not link_matches(sig, "PR 27ZS", "1AB5S0")
    assert not link_matches(sig, "PR 26ZS", "16K08Z")
    assert not link_matches(signature("https://x.es/pr-27zs16k08z"), "PR 27ZS", "16K08Z")


def test_discovered_facts_are_stored_as_evidence(tmp_path):
    site = Site()
    store, _, locator, _, cid = _env(tmp_path, site)
    site.product("Alpha", "AL1001", "10A")
    _watch(store, [(_gtin("840000000001"), "Alpha", "AL1001", "10A", "")])
    locator.enqueue()
    locator.run([cid])
    with store._connect() as connection:
        row = connection.execute("SELECT * FROM discovery_index WHERE check_state='product'").fetchone()
        event = connection.execute("SELECT * FROM locate_events").fetchone()
    facts = json.loads(row["facts"])
    assert facts["brand"] == "Alpha" and row["method"] == "catalog" and row["source_url"].endswith("/marca/alpha")
    assert event["state"] == "done" and event["requests"] == 3
