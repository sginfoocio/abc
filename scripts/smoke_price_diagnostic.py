"""Offline container smoke for private diagnostic storage."""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from zipfile import ZipFile

import pandas as pd

from price_comparator import PriceComparatorStore, normalize_gtin
from price_access import AccessControl
from price_diagnostic import EvidenceError, require_private_database, sanitise
from price_locator import PriceLocator
from price_recovery import apply_manual_reviews, preview_manual_reviews
from price_saved_reevaluation import SavedSampleReevaluation
from price_trial import PriceTrial, manifest
from price_references import EXACT, PROBABLE, REJECTED, classify
from price_url_candidates import _route_11_index, candidate_plan
from price_web_extract import extract_product, product_page_rejection, select_product_variant


def smoke_approved_recovery(path, now):
    store = PriceComparatorStore(path)
    store.seed_defaults()
    access = AccessControl(store, clock=lambda: now)
    access.ensure_defaults()
    cid = next(row["id"] for row in store.competitors().to_dict("records") if row["name"] == "Amevista")
    access.set_method(cid, "ficha", "authorized", actor="smoke", note="Synthetic only")
    frozen = manifest()
    shops = {row["id"]: row["name"] for row in store.competitors().to_dict("records")}
    frozen["competitors"] = shops
    frozen["pairs"] = [{"competitor_id": shop, "ean": ean} for shop in shops for ean in frozen["eans"]]
    limits = {shop: {"daily_limit": 200, "per_minute_limit": 10, "effective_pause": 3} for shop in shops}
    ean = frozen["eans"][0]
    url = "https://www.amevista.com/es/synthetic-fixture"
    with store._connect() as con, con:
        con.executemany("""INSERT INTO products(ean,original_ean,brand,model,color,updated_at)
            VALUES(?,?,'Gucci','GG1660S','001',?)""",
                        [(item, item, (now - timedelta(days=1)).isoformat()) for item in frozen["eans"]])
        con.execute("INSERT INTO watch_groups(name,created_at) VALUES('smoke',?)", (now.isoformat(),))
        con.execute("""INSERT INTO watch_products(ean,group_name,source,added_by,added_at)
            VALUES(?,'smoke','synthetic','smoke',?)""", (ean, now.isoformat()))
        con.execute("""INSERT INTO price_trial(id,status,started_at,expires_at,actor,build_commit,manifest,limits)
            VALUES(1,'expired',?,?,'smoke',?,?,?)""",
                    ((now - timedelta(days=2)).isoformat(), (now - timedelta(days=1)).isoformat(),
                     "a" * 40, json.dumps(frozen), json.dumps(limits)))
        con.execute("""INSERT INTO price_continuity(id,status,prepared_at,started_at,actor,build_commit,
            manifest,limits,interval_hours) VALUES(1,'active',?,?,'smoke',?,?,?,24)""",
                    (now.isoformat(), now.isoformat(), "a" * 40, json.dumps(frozen), json.dumps(limits)))
        con.executemany("""INSERT INTO price_continuity_pairs(continuity_id,competitor_id,ean)
            VALUES(1,?,?)""", [(pair["competitor_id"], pair["ean"]) for pair in frozen["pairs"]])
        con.execute("""INSERT INTO competitor_product_urls(competitor_id,ean,url,model_code,color_code,
            match_status,status,verified_at) VALUES(?,?,?,'GG1660S','001','probable_match','probable',?)""",
                    (cid, ean, url, now.isoformat()))
        con.execute("""INSERT INTO locate_jobs(ean,competitor_id,state,url,created_at,updated_at)
            VALUES(?,?,'review',?,?,?)""", (ean, cid, url, now.isoformat(), now.isoformat()))
    assert PriceLocator(store, clock=lambda: now).review(
        ean, cid, url, accept=True, actor="smoke", note="Synthetic approval", update_price=False) is None
    item = preview_manual_reviews(path, now=now)["approvals_without_request"][0]
    assert item["action"] == "create", item
    args = {"actor": "smoke", "note": "Synthetic recovery", "build_commit": "a" * 40,
            "approved": True, "approval_ids": [item["approval_id"]], "now": now}
    first = apply_manual_reviews(path, **args)["approval_results"][0]
    second = apply_manual_reviews(path, **args)["approval_results"][0]
    assert first["action"] == "created" and first["request_id"] == second["request_id"]
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM manual_review_refreshes").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM access_audit WHERE action='recover_approved_identity'").fetchone()[0] == 1


def smoke_saved_sample_preview(directory):
    now = datetime.now(timezone.utc)
    sample_at = now - timedelta(minutes=5)
    url = "https://opticalh.com/products/rb2140"
    case = {
        "id": 1, "ean": "0805289126577", "competitor_id": "opticalh-com", "url": url,
        "requested_at": (sample_at - timedelta(minutes=1)).isoformat(), "sampled_at": sample_at.isoformat(),
        "sample": {"at": sample_at.isoformat(), "gap": "market_currency", "page": {
            "url": url, "ean_published": "0805289126577", "offer_binding_source": "product_offer",
            "offer_binding_unresolved": False, "availability": "available", "currency": "EUR",
            "price": 109.85, "includes_tax": True, "market": "FR",
        }},
        "commercial_config": {
            "sales_market": "ES", "market_verified": 1, "market_evidence_url": "https://opticalh.com/",
            "market_note": "Synthetic smoke", "tax_policy": "included",
        },
    }
    evidence = Path(directory) / "saved-sample.json"
    evidence.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    store = PriceComparatorStore(Path(directory) / "saved-sample.sqlite3")
    store.seed_defaults()
    with store._connect() as con, con:
        con.execute("""INSERT INTO products(
            ean,original_ean,brand,model,color,caliber,commercial_name,updated_at
        ) VALUES(?,?,?,?,?,?,?,?)""", (
            case["ean"], case["ean"], "Ray-Ban", "RB2140", "901", "50-22", "RB2140",
            (now - timedelta(days=1)).isoformat(),
        ))
    result = SavedSampleReevaluation(store, evidence, clock=lambda: now).preview(1)
    assert result["category"] == "contradiction" and result["current_market"] == "FR"


def main():
    assert (os.geteuid(), os.getegid()) == (1037, 100)
    evidence_path = (Path(__file__).resolve().parents[1] / "docs" / "Precios" / "100_EAN"
                     / "evidencia_replay_133_20261116.zip")
    with ZipFile(evidence_path) as evidence:
        assert evidence.testzip() is None
        readme = evidence.read("LEEME.md").decode("utf-8")
        partial_pages = {name: evidence.read(name) for name in (
            "opticalh-sanitizado.html", "visualclick-sanitizado.html", "mia-burton-sanitizado.html")}
    assert "reconstrucciones sanitizadas parciales" in readme
    assert "no respuestas HTML originales" in readme
    target = {"ean": "0805289126577", "brand": "Ray Ban", "model": "RB2140", "color": "901"}
    optical_url = "https://opticalh.com/products/gafas-de-sol-ray-ban-wayfarer-rb2140-901-50-22-61980"
    optical = extract_product(partial_pages["opticalh-sanitizado.html"].decode("utf-8"), optical_url)
    assert optical and optical.ean_published == normalize_gtin("805289126577") == target["ean"]
    assert optical.variant_id == "57108062601561"
    assert optical.url_trace["selected_variant_url"] == optical_url + "?variant=57108062601561"
    assert classify(target, optical.candidate()).status == EXACT
    visual_url = "https://visual-click.com/products/ray-ban-rb2140-wayfarer-901-32-50"
    visual = extract_product(partial_pages["visualclick-sanitizado.html"].decode("utf-8"), visual_url)
    assert visual and classify(target, visual.candidate()).status == REJECTED
    assert not visual.ean_published and visual.price == 124 and visual.availability == "unknown"
    assert any("no se interpreta como GTIN" in note for note in visual.notes)
    mia_url = "https://miaburton.com/de/sonnenbrillen/ray-ban/wayfarer-rb2140-901-58"
    mia = extract_product(partial_pages["mia-burton-sanitizado.html"].decode("utf-8"), mia_url)
    assert mia and classify(target, mia.candidate()).status == PROBABLE
    assert mia.price == 174 and mia.availability == "unknown" and not mia.ean_published
    miu = classify(
        {"ean": "8056262407691", "brand": "Miu Miu", "model": "MU A06S", "color": "14L09Z"},
        {"brand": "Miu Miu", "model": "GAFASDESOLMIUMIUA06S", "color": "14L09Z",
         "title": "GAFASDESOLMIUMIUA06S 14L09Z"},
    )
    assert miu.status == EXACT
    matrix_routes, excluded_routes, _ = _route_11_index()
    assert len(matrix_routes) == 94 and len(excluded_routes) == 1
    tom_ford_1310 = candidate_plan(
        {"ean": "0889214625984", "brand": "Tom Ford", "model": "FT1310", "color": "01A",
         "category": "All / Gafas de Sol"}, "optica-optima.com")
    assert [item.url for item in tom_ford_1310.runnable] == [
        "https://optica-optima.com/gafas-de-sol/tom-ford-chloe-02-ft1310-01a.html"
    ]
    miu_52ys = candidate_plan(
        {"ean": "8056597886932", "brand": "Miu Miu", "model": "MU 52YS", "color": "7OE01T",
         "category": "All / Gafas de Sol"}, "amevista.com")
    assert [item.url for item in miu_52ys.runnable] == [
        "https://www.amevista.com/es/miu-miu-0mu-52ys-7oe01t"
    ]
    rayban_901_58 = candidate_plan(
        {"ean": "0805289126591", "brand": "Ray Ban", "model": "RB2140", "color": "901/58",
         "category": "All / Gafas de Sol"}, "opticabassol.com")
    assert not rayban_901_58.runnable and all(
        item.state == "probable" for item in rayban_901_58.candidates)
    wrong_rayban_color = candidate_plan(
        {"ean": "0805289126591", "brand": "Ray Ban", "model": "RB2140", "color": "901",
         "category": "All / Gafas de Sol"}, "opticabassol.com")
    assert wrong_rayban_color.reason_code == "route_identity_conflict"
    mua06s_redirect = candidate_plan(
        {"ean": "8056262407691", "brand": "Miu Miu", "model": "MU A06S", "color": "14L09Z",
         "category": "All / Gafas de Sol"}, "edel-optics.es")
    assert not mua06s_redirect.candidates and "redirigió a categoría" in mua06s_redirect.reason
    assert product_page_rejection(
        "", "https://www.edel-optics.es/Gafas-de-sol-Miu-Miu.html")
    visio_bv = classify(
        {"ean": "0889214042422", "brand": "Tom Ford", "model": "FT5634B", "color": "001"},
        {"url": "https://www.visiofactory.es/example", "ean_published": "889214042422",
         "brand": "Tom Ford", "model": "FT5634-B/V", "color": "001",
         "title": "Tom Ford FT5634-B/V 001"},
    )
    assert visio_bv.status == EXACT
    captures = [
        (7, "cf239f7c16c29e5d05743eef881dd34e349a76ecfb73948574aea5b0f5ffa533",
         "https://www.amevista.com/es/gucci-gg1660s-001",
         {"ean": "0889652494821", "brand": "Gucci", "model": "GG1660S", "color": "001"}, 188.5),
        (8, "f2ba1ef3f70781330ae9d57f5052484867db919099262092467a4273a52e2397",
         "https://visual-click.com/products/miu-miu-mu-54ys-zvn20f-80",
         {"ean": "8056262142585", "brand": "Miu Miu", "model": "MU 54YS", "color": "ZVN20F"}, 331),
        (9, "ca16dece3bb302298932d35dfc049795c888dbb8b4adae98f8dbd8720d3446fa",
         "https://domanci.com/es/gafas-de-sol/miu-miu-mu-a51s-5ak20p-75.html",
         {"ean": "8056262412367", "brand": "Miu Miu", "model": "MU A51S", "color": "5AK20P"}, 305.5),
    ]
    fixture_dir = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "web_pages"
    for number, digest, url, product, price in captures:
        content = (fixture_dir / f"partial_20261117_{number}.html").read_bytes().rstrip(b"\r\n")
        assert hashlib.sha256(content).hexdigest() == digest
        page = select_product_variant(extract_product(content.decode("utf-8"), url), product)
        assert page and classify(product, page.candidate()).status == EXACT and page.price == price
        if number == 8:
            assert not page.ean_published and page.availability == "unknown"

    matrix_dir = (Path(__file__).resolve().parents[1] / "docs" / "Precios" / "100_EAN"
                  / "correcciones_29_edel_amevista_20261120")
    with (matrix_dir / "MATRIZ_100x14_ESTADO_CLOUD_20261120.csv").open(
            encoding="utf-8-sig", newline="") as matrix_file:
        matrix = list(csv.DictReader(matrix_file, delimiter=";"))
    assert len(matrix) == 1_400
    assert len({(row["ean"], row["competitor_id"]) for row in matrix}) == 1_400
    assert len({row["snapshot_at"] for row in matrix}) == 1
    assert len({row["product_metadata_at"] for row in matrix}) == 1
    assert len({row["ean"] for row in matrix}) == 100
    assert len({row["competitor_id"] for row in matrix}) == 14
    assert all(row["marca"] and row["modelo"] and row["color"] for row in matrix)
    assert all("caliber" in row for row in matrix)
    current_offers = [row for row in matrix if row["current_comparable_offer"] == "True"]
    assert len(current_offers) == 124
    assert len({row["ean"] for row in current_offers}) == 71
    assert all((row["offer_market"], row["offer_currency"], row["offer_availability"]) ==
               ("ES", "EUR", "available") for row in current_offers)

    with (matrix_dir / "MATRIZ_RUTAS_29_EDEL_AMEVISTA_20261120.csv").open(
            encoding="utf-8-sig", newline="") as matrix_file:
        routes = list(csv.DictReader(matrix_file, delimiter=";"))
    assert len(routes) == 102
    assert len({row["ean"] for row in routes}) == 29
    assert len({(row["ean"], row["competitor_id"]) for row in routes}) == 72
    assert len({row["competidor"] for row in routes}) == 12
    assert len({row["product_metadata_at"] for row in routes}) == 1
    assert all(row["catalog_brand"] and row["catalog_model"] and row["catalog_color"] for row in routes)
    assert all("Ruta documental solamente" in row["result_and_limitation"] for row in routes)

    with tempfile.TemporaryDirectory(dir=os.environ["CLOUD_SMOKE_DIRECTORY"]) as directory:
        os.umask(0o077)
        path = Path(directory) / "diagnostic.sqlite3"
        store = PriceComparatorStore(path)
        path.chmod(0o644)
        try:
            require_private_database(path)
        except EvidenceError:
            pass
        else:
            raise AssertionError("El almacenamiento público debe rechazarse")
        path.chmod(0o600)
        url = "https://example.test/products/fixture"
        node = {"@type": "Product", "name": "Fixture", "url": url,
                "offers": {"@type": "Offer", "url": url + "?variant=1",
                           "price": "120", "priceCurrency": "EUR"}}
        text = ('<html><head><script type="application/ld+json">' + json.dumps(node)
                + '</script></head><body><input name="csrf" value="sensitive">'
                  '<div>UPC: 889652429687</div></body></html>')
        fetched = SimpleNamespace(content=text.encode(), text=text, url=url, requested_url=url,
                                  redirects=[], status=200)
        metadata, artifact = sanitise(fetched, 2 * 1024 * 1024)
        assert metadata["artifact_kind"] == "sanitizado_parcial_jsonld"
        assert metadata["sanitization_version"] == 3 and metadata["unscoped_eans"] == ["889652429687"]
        assert b"Offer" in artifact and b"variant=1" in artifact and b"sensitive" not in artifact
        node["offers"] = {"@type": "Offer", "price": "120", "priceCurrency": "EUR",
                          "eligibleCustomerType": "private member",
                          "shippingDetails": {"@type": "OfferShippingDetails",
                                              "shippingDestination": {"@type": "DefinedRegion",
                                                                      "addressCountry": "FR"}}}
        text = '<html><script type="application/ld+json">' + json.dumps(node) + '</script></html>'
        fetched = SimpleNamespace(content=text.encode(), text=text, url=url, requested_url=url,
                                  redirects=[], status=200)
        _, sanitized = sanitise(fetched, 2 * 1024 * 1024)
        replay = extract_product(sanitized.decode(), url)
        assert replay and replay.price is None and b"private member" not in sanitized
        del node["offers"]["eligibleCustomerType"]
        text = '<html><script type="application/ld+json">' + json.dumps(node) + '</script></html>'
        fetched = SimpleNamespace(content=text.encode(), text=text, url=url, requested_url=url,
                                  redirects=[], status=200)
        _, sanitized = sanitise(fetched, 2 * 1024 * 1024)
        replay = extract_product(sanitized.decode(), url)
        assert replay and replay.market == "FR"
        artifact = partial_pages["opticalh-sanitizado.html"]
        artifact_metadata = {"artifact_kind": "sanitizado_parcial_jsonld",
                             "artifact_sha256": hashlib.sha256(artifact).hexdigest()}
        now = datetime.now(timezone.utc)
        with store._connect() as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("INSERT INTO products(ean,original_ean,updated_at) VALUES('0805289126577','0805289126577',?)",
                        (now.isoformat(),))
            con.execute("INSERT INTO competitors(id,name,url,domain) VALUES('fixture','Fixture',?,?)", (url, "example.test"))
            con.execute("""INSERT INTO price_continuity(id,status,prepared_at,actor,build_commit,manifest,limits,interval_hours)
                VALUES(1,'active',?,'fixture',?,'{}','{}',24)""", (now.isoformat(), "a" * 40))
            con.execute("""INSERT INTO price_diagnostic_requests
                (continuity_id,competitor_id,ean,url,actor,note,build_commit,status,created_at,artifact,expires_at,result)
                VALUES(1,'fixture','0805289126577',?,'fixture','offline',?,'captured',?,?,?,?)""",
                        (url, "a" * 40, now.isoformat(), artifact, (now + timedelta(days=1)).isoformat(),
                         json.dumps(artifact_metadata)))
            con.commit()
            require_private_database(path)
            for suffix in ("-wal", "-shm"):
                companion = Path(str(path) + suffix)
                assert companion.is_file() and stat.S_IMODE(companion.stat().st_mode) & 0o077 == 0
                companion.chmod(0o644)
                try:
                    require_private_database(path)
                except EvidenceError:
                    pass
                else:
                    raise AssertionError(f"El auxiliar {suffix} debe ser privado")
                companion.chmod(0o600)
            assert PriceTrial(store).diagnostic_artifact(1) == artifact
        assert PriceTrial(PriceComparatorStore(path)).diagnostic_artifact(1) == artifact
        with store._connect() as con, con:
            con.execute("""INSERT INTO locate_jobs(id,ean,competitor_id,created_at,updated_at)
                VALUES(1,'0805289126577','fixture',?,?)""", (now.isoformat(), now.isoformat()))
            con.execute("""INSERT INTO manual_review_refreshes
                (id,locate_job_id,ean,competitor_id,url,actor,reason,requested_at,updated_at)
                VALUES(1,1,'0805289126577','fixture',?,'fixture','offline',?,?)""",
                        (url, now.isoformat(), now.isoformat()))
        frame = pd.DataFrame([{"EAN": "0805289126577", "Competidor": "fixture",
                               "Precio": 120, "Moneda": "EUR", "Mercado": "ES", "URL": url,
                               "Disponibilidad": "available", "Estado coincidencia": "exact_match",
                               "Evidencia": "Offline fixture", "Método": "web:monitor",
                               "Fecha consulta": now.isoformat()}])
        first = (1, "execution-one", now.isoformat(), "digest-one", '{"price":120}')
        second_at = (now + timedelta(days=1)).isoformat()
        second = (1, "execution-two", second_at, "digest-two", '{"price":120}')
        assert not store.import_offers(frame, validate=lambda con: None, review_sample=first).rejected
        frame["Fecha consulta"] = second_at
        assert not store.import_offers(frame, validate=lambda con: None, review_sample=second).rejected
        assert not store.import_offers(frame, validate=lambda con: None, review_sample=second).rejected
        with PriceComparatorStore(path)._connect() as con:
            assert con.execute("SELECT COUNT(*) FROM manual_review_recorded_sample_events").fetchone()[0] == 2
            assert con.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
            assert con.execute("SELECT checks FROM offer_checks").fetchone()[0] == 1
        smoke_approved_recovery(Path(directory) / "recovery.sqlite3", now)
        smoke_saved_sample_preview(directory)
    print("Private SQLite and archived partial JSON-LD evidence verified offline as UID 1037")


if __name__ == "__main__":
    main()
