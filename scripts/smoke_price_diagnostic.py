"""Offline container smoke for private diagnostic storage."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

from price_comparator import PriceComparatorStore
from price_diagnostic import EvidenceError, require_private_database, sanitise
from price_trial import PriceTrial


def main():
    assert os.geteuid() == 1037
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
        now = datetime.now(timezone.utc)
        with store._connect() as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("INSERT INTO products(ean,original_ean,updated_at) VALUES('0805289126577','0805289126577',?)",
                        (now.isoformat(),))
            con.execute("INSERT INTO competitors(id,name,url,domain) VALUES('fixture','Fixture',?,?)", (url, "example.test"))
            con.execute("""INSERT INTO price_continuity(id,status,prepared_at,actor,build_commit,manifest,limits,interval_hours)
                VALUES(1,'active',?,'fixture',?,'{}','{}',24)""", (now.isoformat(), "a" * 40))
            con.execute("""INSERT INTO price_diagnostic_requests
                (continuity_id,competitor_id,ean,url,actor,note,build_commit,status,created_at,artifact,expires_at)
                VALUES(1,'fixture','0805289126577',?,'fixture','offline',?,'captured',?,?,?)""",
                        (url, "a" * 40, now.isoformat(), artifact, (now + timedelta(days=1)).isoformat()))
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
    print("Private SQLite and offline sanitized evidence verified as UID 1037")


if __name__ == "__main__":
    main()
