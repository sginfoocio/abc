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

from price_comparator import PriceComparatorStore, normalize_gtin
from price_diagnostic import EvidenceError, require_private_database, sanitise
from price_trial import PriceTrial
from price_references import EXACT, PROBABLE, REJECTED, classify
from price_web_extract import extract_product


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
    assert not visual.ean_published and visual.price is None and visual.availability == "unknown"
    assert any("no se interpreta como GTIN" in note for note in visual.notes)
    mia_url = "https://miaburton.com/de/sonnenbrillen/ray-ban/wayfarer-rb2140-901-58"
    mia = extract_product(partial_pages["mia-burton-sanitizado.html"].decode("utf-8"), mia_url)
    assert mia and classify(target, mia.candidate()).status == PROBABLE
    assert mia.price == 174 and mia.availability == "unknown" and not mia.ean_published

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
    print("Private SQLite and archived partial JSON-LD evidence verified offline as UID 1037")


if __name__ == "__main__":
    main()
