"""Offline container smoke for private diagnostic storage."""
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
from price_diagnostic import EvidenceError, require_private_database, sanitise
from price_trial import PriceTrial
from price_references import EXACT, PROBABLE, REJECTED, classify
from price_web_extract import extract_product, select_product_variant


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
                + '</script></head><body><input name="csrf" value="sensitive"></body></html>')
        fetched = SimpleNamespace(content=text.encode(), text=text, url=url, requested_url=url,
                                  redirects=[], status=200)
        metadata, artifact = sanitise(fetched, 2 * 1024 * 1024)
        assert metadata["artifact_kind"] == "sanitizado_parcial_jsonld"
        assert b"Offer" in artifact and b"variant=1" in artifact and b"sensitive" not in artifact
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
    print("Private SQLite and archived partial JSON-LD evidence verified offline as UID 1037")


if __name__ == "__main__":
    main()
