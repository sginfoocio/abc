"""Phase 3: wider incremental discovery (listing kinds, controlled sub-listings, adapters, change detection,
retired pages) and periodic scheduling of the location queue. Every site is simulated: no real network."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from price_locator import LEASE, SCHEDULE_ENV, UNAVAILABLE_TEXT, canonical
from tests.test_price_locator import BASE, ROBOTS, Crash, _env, _jobs, _urls, _watch
from tests.test_price_watch import _gtin
from tests.web_fixtures import page

CATEGORY = BASE + "/gafas-de-sol"
FOLLOW = r"^/gafas-de-sol/[a-z-]+$"


class Shop:
    """Listings (brand, collection, category with sub-listings) in three pagination styles, plus fichas."""

    def __init__(self, robots=ROBOTS):
        self.robots = robots
        self.pages: dict[str, str] = {}
        self.status: dict[str, int] = {}
        self.calls: list[str] = []
        self.crash_after: int | None = None

    def product(self, brand, model, color, *, caliber="54-18", gtin=None, stock=True, html=None, path=None):
        path = path or "/gafas/" + "-".join([*brand.lower().split(), *model.lower().split(), color.lower(),
                                             caliber]) + ".html"
        self.pages[BASE + path] = html or page(name=f"{brand} {model} {color} {caliber}", price=120, brand=brand,
                                               gtin=gtin, stock=stock)
        return BASE + path

    @staticmethod
    def _url(path, number, style, per_page):
        if number == 1:
            return BASE + path
        return {"page": f"{BASE}{path}?page={number}", "rel": f"{BASE}{path}/pagina/{number}",
                "data": f"{BASE}{path}?offset={(number - 1) * per_page}"}[style]

    def listing(self, path, links, *, per_page=0, style="page", extra=""):
        """``links``: (href, text). Several pages when ``per_page`` is set."""
        size = per_page or max(len(links), 1)
        chunks = [links[i:i + size] for i in range(0, len(links), size)] or [[]]
        for number, chunk in enumerate(chunks, 1):
            body = "".join(f'<a href="{href}">{text}</a>' for href, text in chunk)
            body += '<a href="/carrito">Carrito</a><a href="/">Inicio</a>' + extra
            if number < len(chunks):
                following = self._url(path, number + 1, style, size)
                body += {"page": f'<a href="{following}">Siguiente</a>',
                         "rel": f'<link rel="next" href="{following}">',
                         "data": f'<div class="more" data-next-url="{following}"></div>'}[style]
            self.pages[self._url(path, number, style, size)] = f"<html><head></head><body>{body}</body></html>"

    def __call__(self, url, headers, timeout):
        if self.crash_after is not None and len(self.calls) >= self.crash_after:
            raise Crash()
        self.calls.append(url)
        if url in self.status:
            return self.status[url], {"Retry-After": "120"}, b"blocked"
        if url.endswith("/robots.txt"):
            return 200, {}, self.robots.encode()
        if url in self.pages:
            return 200, {"Content-Type": "text/html"}, self.pages[url].encode()
        return 404, {}, b""

    def count(self, url):
        return self.calls.count(url)

    def fichas(self):
        return [call for call in self.calls if "/gafas/" in call or "/p/" in call]


def _env3(tmp_path, shop, *, methods=("ficha", "catalog")):
    return _env(tmp_path, shop, methods=methods, template=None)


def _ean(*parts):
    return _gtin(("84" + "".join(str(part) for part in parts) + "0" * 12)[:12])


def _source(locator, cid, template, **kwargs):
    locator.save_source(cid, "catalog", template, actor="ana", note="Listado permitido", **kwargs)


def _pages(store, cid):
    with store._connect() as connection:
        return pd.read_sql_query("SELECT * FROM discovery_pages WHERE competitor_id=?", connection,
                                 params=(cid,)).set_index("url")


@pytest.mark.parametrize("style", ["page", "rel", "data"])
def test_paginated_listings_are_followed_once(tmp_path, style):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    links = [(shop.product("Alpha", f"AL{1000 + i}", "10A"), f"Alpha AL{1000 + i} 10A") for i in range(60)]
    shop.listing("/marca/alpha", links, per_page=25, style=style)
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")
    rows = [(_ean(1, i), "Alpha", f"AL{1000 + i}", "10A", "") for i in (1, 30, 59)]  # pages 1, 2 and 3
    _watch(store, rows)
    locator.run([cid])
    jobs = _jobs(store, cid)
    assert set(jobs["state"]) == {"done"}
    listing_calls = [call for call in shop.calls if "/marca/alpha" in call]
    assert len(listing_calls) == 3 == len(set(listing_calls))  # three pages, each read once
    assert len(shop.fichas()) == 3  # only the watched fichas, never the other 57 products


def test_category_and_collection_tiers_and_controlled_sublistings(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    alpha = shop.product("Alpha", "AL1001", "10A")
    beta = shop.product("Beta", "BE2001", "20B")
    gamma = shop.product("Gamma", "GA3001", "001")
    zeta = shop.product("Zeta", "ZE4001", "001")
    shop.listing("/marca/alpha", [(alpha, "Alpha AL1001 10A")])
    shop.listing("/coleccion/verano", [(beta, "Beta BE2001 20B")])
    shop.listing("/gafas-de-sol", [("/gafas-de-sol/gamma", "Gamma"), ("/gafas-de-sol/zeta", "Zeta"),
                                   ("/gafas-de-sol/beta", "Beta"), ("/ofertas", "Ofertas")])
    shop.listing("/gafas-de-sol/gamma", [(gamma, "Gamma GA3001 001")])
    shop.listing("/gafas-de-sol/zeta", [(zeta, "Zeta ZE4001 001")])
    shop.listing("/gafas-de-sol/beta", [(beta, "Beta BE2001 20B")])
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1)
    _source(locator, cid, BASE + "/coleccion/verano", kind="collection")
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")

    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "done"
    # The brand page answered: neither the collection nor the whole category were read.
    assert shop.count(BASE + "/coleccion/verano") == 0 and shop.count(CATEGORY) == 0

    _watch(store, [(_ean(2, 1), "Beta", "BE2001", "20B", ""), (_ean(3, 1), "Gamma", "GA3001", "001", "")])
    locator.run([cid])
    jobs = _jobs(store, cid).set_index("ean")
    assert set(jobs["state"]) == {"done"}
    assert jobs.loc[_ean(2, 1), "url"] == beta and jobs.loc[_ean(3, 1), "url"] == gamma
    assert shop.count(BASE + "/coleccion/verano") == 1 and shop.count(CATEGORY) == 1
    # Sub-listings are followed only when they name a watched brand: Zeta and «Ofertas» are never read.
    assert shop.count(BASE + "/gafas-de-sol/gamma") == 1
    assert shop.count(BASE + "/gafas-de-sol/zeta") == 0 and shop.count(BASE + "/ofertas") == 0
    assert shop.count(zeta) == 0
    children = json.loads(_pages(store, cid).loc[CATEGORY, "children"])
    assert BASE + "/gafas-de-sol/zeta" not in children and BASE + "/gafas-de-sol/gamma" in children


def test_sublisting_depth_is_bounded(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    deep = shop.product("Alpha", "AL1001", "10A")
    shop.listing("/gafas-de-sol", [("/gafas-de-sol/alpha", "Alpha")])
    shop.listing("/gafas-de-sol/alpha", [("/gafas-de-sol/alpha-sol", "Alpha sol")])
    shop.listing("/gafas-de-sol/alpha-sol", [(deep, "Alpha AL1001 10A")])
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1)
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "not_found"
    assert shop.count(BASE + "/gafas-de-sol/alpha-sol") == 0  # depth 2 not allowed by the source
    with pytest.raises(ValueError, match="sublistados"):
        _source(locator, cid, CATEGORY, kind="category", max_depth=1)
    with pytest.raises(ValueError, match="Profundidad"):
        _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=9)
    with pytest.raises(ValueError, match="Patrón"):
        _source(locator, cid, CATEGORY, kind="category", follow_pattern="([", max_depth=1)
    with pytest.raises(ValueError, match="Adaptador"):
        _source(locator, cid, CATEGORY, kind="category", adapter="desconocido")


def test_duplicate_links_are_indexed_and_checked_once(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    url = shop.product("Alpha", "AL1001", "10A")
    other = shop.product("Alpha", "AL1002", "10A")
    path = url.removeprefix(BASE)
    shop.listing("/marca/alpha", [(path, "Alpha AL1001 10A"), (path + "#opiniones", "Opiniones"),
                                  (url + "?utm_source=news&utm_medium=mail", "Alpha AL1001 10A"),
                                  (other, "Alpha AL1002 10A"), (path, "Alpha AL1001 10A")], per_page=3)
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", ""), (_ean(1, 2), "Alpha", "AL1002", "10A", "")])
    locator.run([cid])
    assert set(_jobs(store, cid)["state"]) == {"done"}
    with store._connect() as connection:
        indexed = [row[0] for row in connection.execute("SELECT url FROM discovery_index WHERE competitor_id=?",
                                                        (cid,))]
    assert sorted(indexed) == sorted([url, other])
    assert shop.count(url) == 1 and not any("utm_" in call or "#" in call for call in shop.calls)
    assert canonical(url + "?utm_source=x&page=2#a") == url + "?page=2"


def test_modified_and_unchanged_listings(tmp_path):
    shop = Shop()
    store, _, locator, clock, cid = _env3(tmp_path, shop)
    listing = BASE + "/marca/alpha"
    first = shop.product("Alpha", "AL1001", "10A")
    shop.listing("/marca/alpha", [(first, "Alpha AL1001 10A")])
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand", refresh_hours=24)
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    assert shop.count(listing) == 1

    # Still valid: a new product (not yet published) costs no listing request.
    _watch(store, [(_ean(1, 2), "Alpha", "AL1002", "10A", "")])
    locator.run([cid])
    assert shop.count(listing) == 1 and _jobs(store, cid).iloc[1]["state"] == "not_found"

    # The competitor publishes it: once the copy expires the listing is read again and the change detected.
    second = shop.product("Alpha", "AL1002", "10A")
    shop.listing("/marca/alpha", [(first, "Alpha AL1001 10A"), (second, "Alpha AL1002 10A")])
    clock.advance(days=7, hours=1)
    locator.run([cid])
    stored = _pages(store, cid).loc[listing]
    assert shop.count(listing) == 2 and _jobs(store, cid).iloc[1]["state"] == "done"
    assert stored["unchanged_runs"] == 0 and stored["changed_at"] == stored["fetched_at"]
    changed_at = stored["changed_at"]

    # Unchanged at its next refresh: it stays valid twice as long (48 h), then 96 h at most.
    clock.advance(hours=25)
    _watch(store, [(_ean(1, 3), "Alpha", "AL1003", "10A", "")])
    locator.run([cid])
    stored = _pages(store, cid).loc[listing]
    assert shop.count(listing) == 3 and stored["unchanged_runs"] == 1 and stored["changed_at"] == changed_at
    clock.advance(hours=25)
    _watch(store, [(_ean(1, 4), "Alpha", "AL1004", "10A", "")])
    locator.run([cid])
    assert shop.count(listing) == 3  # 25 h < 48 h: not read again
    clock.advance(hours=24)
    _watch(store, [(_ean(1, 5), "Alpha", "AL1005", "10A", "")])
    locator.run([cid])
    assert shop.count(listing) == 4 and _pages(store, cid).loc[listing, "unchanged_runs"] == 2


def test_retired_pages_trigger_relocation_through_the_index(tmp_path):
    shop = Shop()
    store, _, locator, clock, cid = _env3(tmp_path, shop)
    old = shop.product("Alpha", "AL1001", "10A", path="/gafas/alpha-al1001-10a-old.html")
    shop.listing("/marca/alpha", [(old, "Alpha AL1001 10A")])
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand", refresh_hours=24)
    ean = _ean(1, 1)
    _watch(store, [(ean, "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["url"] == old

    # The page is withdrawn and the product republished elsewhere. Monitoring (phase 4) reports the 404.
    del shop.pages[old]
    new = shop.product("Alpha", "AL1001", "10A", path="/gafas/alpha-al1001-10a-nueva.html")
    shop.listing("/marca/alpha", [(new, "Alpha AL1001 10A")])
    assert locator.retire(cid, old, "HTTP 404 en la monitorización") == 1
    assert locator.enqueue()["relocate"] == 1
    clock.advance(hours=25)
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert (job["kind"], job["state"], job["url"]) == ("relocate", "done", new)
    statuses = _urls(store, cid).set_index("url")["status"]
    assert statuses[old] == "replaced" and statuses[new] == "verified"
    with store._connect() as connection:
        gone = connection.execute("SELECT check_state,gone_at FROM discovery_index WHERE url=?", (old,)).fetchone()
    assert gone["check_state"] == "gone" and gone["gone_at"]
    assert shop.count(old) == 1  # never fetched again after it was retired


def test_listed_but_withdrawn_ficha_is_marked_gone(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    shop.listing("/marca/alpha", [(BASE + "/gafas/alpha-al1001-10a.html", "Alpha AL1001 10A")])  # stale card
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    assert _jobs(store, cid).iloc[0]["state"] == "not_found"  # not "missing product": retried later
    with store._connect() as connection:
        row = connection.execute("SELECT check_state,gone_at FROM discovery_index WHERE competitor_id=?",
                                 (cid,)).fetchone()
    assert row["check_state"] == "gone" and row["gone_at"]


def test_reference_with_several_colours_maps_each_colour_to_its_page(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    links = []
    for color in ("10A", "20B", "93E", "001"):
        for caliber in ("52-18", "55-18"):  # calibre variants never decide identity
            links.append((shop.product("Alpha", "AL1001", color, caliber=caliber), f"Alpha AL1001 {color}"))
    shop.listing("/marca/alpha", links)
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")
    rows = [(_ean(1, i), "Alpha", "AL1001", color, "55-18") for i, color in enumerate(("10A", "20B", "93E"))]
    _watch(store, rows)
    locator.run([cid])
    jobs = _jobs(store, cid).set_index("ean")
    assert set(jobs["state"]) == {"done"}
    for ean, _, _, color, _ in rows:
        assert f"-{color.lower()}-55-18" in jobs.loc[ean, "url"]  # own colour, matching calibre first
    assert jobs["url"].is_unique and not any("-001-" in call for call in shop.calls)


@pytest.mark.parametrize("block,state,kind", [("captcha", "blocked", "captcha"), (403, "blocked", "http_403"),
                                              (429, "waiting", "http_429")])
def test_blocks_on_a_sublisting_suspend_the_competitor(tmp_path, block, state, kind):
    shop = Shop()
    store, access, locator, clock, cid = _env3(tmp_path, shop)
    alpha = shop.product("Alpha", "AL1001", "10A")
    beta = shop.product("Beta", "BE2001", "20B")
    shop.listing("/gafas-de-sol", [("/gafas-de-sol/alpha", "Alpha"), ("/gafas-de-sol/beta", "Beta")])
    shop.listing("/gafas-de-sol/alpha", [(alpha, "Alpha AL1001 10A")])
    shop.listing("/gafas-de-sol/beta", [(beta, "Beta BE2001 20B")])
    blocked = BASE + "/gafas-de-sol/alpha"
    if block == "captcha":
        shop.pages[blocked] = "<html><body>Please verify you are human (captcha)</body></html>"
    else:
        shop.status[blocked] = block
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1)
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", ""), (_ean(2, 1), "Beta", "BE2001", "20B", "")])
    report = locator.run([cid])
    assert cid in report.stopped and set(_jobs(store, cid)["state"]) == {state}
    assert shop.count(blocked) == 1 and shop.count(BASE + "/gafas-de-sol/beta") == 0 and not shop.fichas()
    assert access.incidents(open_only=True).iloc[0]["kind"] == kind
    calls = len(shop.calls)
    clock.advance(minutes=1)
    locator.run([cid])
    assert len(shop.calls) == calls  # suspended: not a single request, nothing bypassed
    if block == 429:
        del shop.status[blocked]
        clock.advance(hours=2)
        locator.run([cid])
        assert set(_jobs(store, cid)["state"]) == {"done"}
        assert shop.count(CATEGORY) == 1  # the category copy is reused after the pause


def test_interrupted_crawl_is_resumed_without_repeating_requests(tmp_path):
    shop = Shop()
    store, _, locator, clock, cid = _env3(tmp_path, shop)
    rows, brand_links = [], []
    for b, brand in enumerate(("Alpha", "Beta", "Gamma", "Delta")):
        brand_links.append((f"/gafas-de-sol/{brand.lower()}", brand))
        links = []
        for m in range(6):
            model = f"{brand[:2].upper()}{1000 + m}"
            links.append((shop.product(brand, model, "10A"), f"{brand} {model} 10A"))
            rows.append((_ean(b, m), brand, model, "10A", ""))
        shop.listing(f"/gafas-de-sol/{brand.lower()}", links, per_page=3)
    shop.listing("/gafas-de-sol", brand_links)
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1, max_pages=20)
    _watch(store, rows)
    shop.crash_after = 6  # robots + category + 4 sub-listing pages, then the process dies
    with pytest.raises(Crash):
        locator.run([cid])
    shop.crash_after = None
    clock.advance(minutes=LEASE.total_seconds() / 60 + 1)
    locator.run([cid])
    assert set(_jobs(store, cid)["state"]) == {"done"}
    listing_calls = [call for call in shop.calls if "/gafas-de-sol" in call]
    assert len(listing_calls) == len(set(listing_calls)) == 9  # 1 category + 4 brands × 2 pages, once each
    assert len(shop.fichas()) == len(set(shop.fichas())) == 24


def test_competitor_without_authorised_listing_method_makes_no_request(tmp_path):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop, methods=("ficha",))
    shop.listing("/gafas-de-sol", [("/gafas-de-sol/alpha", "Alpha")])
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1)
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == "unavailable" and UNAVAILABLE_TEXT in job["detail"]
    assert "catálogo" in job["detail"].lower()
    assert shop.calls == []


@pytest.mark.parametrize("adapter,state", [("generic", "not_found"), ("itemlist", "done")])
def test_itemlist_adapter_for_cards_without_identity(tmp_path, adapter, state):
    shop = Shop()
    store, _, locator, _, cid = _env3(tmp_path, shop)
    url = shop.product("Alpha", "AL1001", "10A", path="/p/1001")
    ld = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": 1,
         "item": {"@type": "Product", "url": "/p/1001", "name": "AL1001 10A", "brand": {"name": "Alpha"}}}]}
    shop.listing("/marca/alpha", [("/p/1001", "Ver")],
                 extra=f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand", adapter=adapter)
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    locator.run([cid])
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == state
    if state == "done":
        assert job["url"] == url


BRANDS = ("Alpha", "Beta", "Gamma", "Delta", "Omega")
WATCHED = ("10A", "20B", "30C", "93E", "001")


def _catalogue(shop):
    rows, extra, brand_links = [], [], []
    for b, brand in enumerate(BRANDS + ("Zeta", "Kappa")):
        brand_links.append((f"/gafas-de-sol/{brand.lower()}", brand))
        links = []
        for m in range(20):
            model = f"{brand[:2].upper()}{1000 + m}"
            for c, color in enumerate(WATCHED + ("99Z",)):
                links.append((shop.product(brand, model, color, caliber=f"{50 + c}-18"), f"{brand} {model} {color}"))
                if brand in BRANDS:
                    (rows if color != "99Z" else extra).append((_ean(b, f"{m:02d}", c), brand, model, color, ""))
        shop.listing(f"/gafas-de-sol/{brand.lower()}", links, per_page=40)
    shop.listing("/gafas-de-sol", brand_links)
    return rows, extra


def _scenario(tmp_path, shop):
    store, access, locator, clock, cid = _env3(tmp_path, shop)
    _source(locator, cid, CATEGORY, kind="category", follow_pattern=FOLLOW, max_depth=1, max_pages=50)
    return store, locator, clock, cid


def test_500_eans_incremental_index_reduces_requests_in_following_runs(tmp_path):
    shop = Shop()
    rows, extra = _catalogue(shop)
    assert len(rows) == 500 and len({row[0] for row in rows}) == 500 and len(extra) == 100
    store, locator, clock, cid = _scenario(tmp_path / "a", shop)
    _watch(store, rows)

    # First run, interrupted and recovered.
    shop.crash_after = 200
    with pytest.raises(Crash):
        locator.run([cid], max_jobs=10_000)
    shop.crash_after = None
    clock.advance(minutes=LEASE.total_seconds() / 60 + 1)
    locator.run([cid], max_jobs=10_000)
    jobs = _jobs(store, cid)
    assert len(jobs) == 500 and set(jobs["state"]) == {"done"} and jobs["url"].is_unique
    listings = [call for call in shop.calls if "/gafas-de-sol" in call]
    assert len(listings) == len(set(listings)) == 1 + 5 * 3  # category + watched brands only (Zeta/Kappa never)
    assert len(shop.fichas()) == len(set(shop.fichas())) == 500
    first_run = len(shop.calls)

    # Second run: everything verified, nothing requested.
    locator.enqueue()
    locator.run([cid], max_jobs=10_000)
    assert len(shop.calls) == first_run

    # 100 new EANs already present in the index: only their fichas are requested, no listing at all.
    _watch(store, extra)
    locator.run([cid], max_jobs=10_000)
    incremental = shop.calls[first_run:]
    assert not [call for call in incremental if "/gafas-de-sol" in call]
    assert not (set(incremental) - set(shop.fichas()))  # valid robots response is shared across runs
    assert len(incremental) == 100 and set(_jobs(store, cid)["state"]) == {"done"}

    # Same 100 EANs on an empty index: listings must be read again.
    cold = Shop()
    _catalogue(cold)
    cold_store, cold_locator, _, cold_cid = _scenario(tmp_path / "b", cold)
    _watch(cold_store, extra)
    cold_locator.run([cold_cid], max_jobs=10_000)
    assert set(_jobs(cold_store, cold_cid)["state"]) == {"done"}
    assert len(incremental) < len(cold.calls) == 100 + 16 + 1  # fichas + listings + robots.txt


# periodic scheduling ---------------------------------------------------------------------------------------
def _scheduled(tmp_path, monkeypatch, *, enabled=True):
    shop = Shop()
    alpha = shop.product("Alpha", "AL1001", "10A")
    shop.listing("/marca/alpha", [(alpha, "Alpha AL1001 10A")])
    store, access, locator, clock, cid = _env3(tmp_path, shop)
    _source(locator, cid, BASE + "/marca/{brand}", kind="brand")
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    if enabled:
        monkeypatch.setenv(SCHEDULE_ENV, "1")
    else:
        monkeypatch.delenv(SCHEDULE_ENV, raising=False)
    return shop, store, locator, clock, cid


def test_schedule_is_off_by_default(tmp_path, monkeypatch):
    shop, store, locator, _, cid = _scheduled(tmp_path, monkeypatch, enabled=False)
    locator.configure_schedule(cid, enabled=True, actor="ana")
    result = locator.tick()
    assert SCHEDULE_ENV in result["skipped"] and shop.calls == [] and locator.runs().empty
    # Switched on at server level but no competitor enabled: still nothing.
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    locator.configure_schedule(cid, enabled=False, actor="ana")
    assert locator.tick()["runs"] == [] and shop.calls == []
    assert set(locator.schedule()["enabled"]) == {0}


def test_tick_runs_due_competitors_and_reschedules(tmp_path, monkeypatch):
    shop, store, locator, clock, cid = _scheduled(tmp_path, monkeypatch)
    locator.configure_schedule(cid, enabled=True, interval_hours=12, max_jobs=50, deadline_minutes=5, actor="ana")
    with pytest.raises(ValueError):
        locator.configure_schedule(cid, enabled=True, interval_hours=0, actor="ana")
    started = clock()
    result = locator.tick()
    assert [run["competitor_id"] for run in result["runs"]] == [cid]
    assert _jobs(store, cid).iloc[0]["state"] == "done"
    plan = locator.schedule().set_index("competitor_id").loc[cid]
    assert plan["next_run_at"] == (started + pd.Timedelta(hours=12)).isoformat(timespec="seconds")
    run = locator.runs().iloc[0]
    assert (run["status"], run["trigger"], run["processed"]) == ("ok", "schedule", 1)
    # Other competitors keep their jobs untouched: they are not scheduled.
    with store._connect() as connection:
        others = connection.execute("SELECT DISTINCT state FROM locate_jobs WHERE competitor_id<>?", (cid,))
        assert {row[0] for row in others} == {"pending"}
        audit = connection.execute("SELECT actor FROM access_audit WHERE action='locate_schedule'").fetchall()
    assert len(audit) == 1  # the rejected configuration was neither saved nor audited

    calls = len(shop.calls)
    assert locator.tick()["runs"] == []  # not due yet
    clock.advance(hours=13)
    assert len(locator.tick()["runs"]) == 1
    assert len(shop.calls) == calls  # due again, but the verified page is reused: no request


def test_tick_recovers_an_interrupted_run_without_duplicates(tmp_path, monkeypatch):
    shop, store, locator, clock, cid = _scheduled(tmp_path, monkeypatch)
    locator.configure_schedule(cid, enabled=True, actor="ana")
    shop.crash_after = 2  # robots.txt and listing, then the process dies before the ficha
    with pytest.raises(Crash):
        locator.tick()
    assert locator.runs().iloc[0]["status"] == "running"
    shop.crash_after = None
    clock.advance(minutes=LEASE.total_seconds() / 60 + 1)
    result = locator.tick()  # the lock was released by the crash; the run is due again (not rescheduled)
    assert result["interrupted"] == 1 and len(result["runs"]) == 1
    assert list(locator.runs()["status"]) == ["ok", "interrupted"]
    assert _jobs(store, cid).iloc[0]["state"] == "done"
    assert shop.count(BASE + "/marca/alpha") == 1  # the listing read before the crash is reused


def test_domanci_default_source_stays_inert_without_documented_authorisation(tmp_path, monkeypatch):
    shop = Shop()
    store, access, locator, clock, _ = _env(tmp_path, shop, methods=(), template=None)
    cid = store.competitors().set_index("name").loc["Domanci", "id"]
    assert locator.seed_sources() >= 1
    source = locator.sources().set_index("competitor_id").loc[cid]
    assert source["kind"] == "brand" and source["max_depth"] == 0
    _watch(store, [(_ean(1, 1), "Alpha", "AL1001", "10A", "")])
    monkeypatch.setenv(SCHEDULE_ENV, "1")
    locator.configure_schedule(cid, enabled=True, actor="ana")
    locator.tick()
    job = _jobs(store, cid).iloc[0]
    assert job["state"] == "unavailable" and UNAVAILABLE_TEXT in job["detail"]
    assert shop.calls == []
