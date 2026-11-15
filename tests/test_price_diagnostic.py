import json
import os
from pathlib import Path

import pytest

from price_diagnostic import EvidenceError, has_capacity, require_private_database, safe_url, sanitise
from price_web_fetch import Fetched


def test_sanitisation_preserves_relationships_and_redacts_irrelevant_content():
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "ProductGroup", "@id": "#g", "hasVariant": {"@id": "#v"}},
        {"@type": "Product", "@id": "#v", "sku": "A1", "offers": {"@id": "#o"}},
        {"@type": "Offer", "@id": "#o", "price": "120", "priceCurrency": "EUR"},
        {"@type": "Person", "name": "Private Name"},
    ]}
    text = ('<html><head><link rel="canonical" href="https://shop.example/p?token=private">'
            '<script type="application/ld+json">' + json.dumps(graph)
            + '</script></head><body><input name="csrf" value="private"></body></html>')
    fetched = Fetched("https://shop.example/p", 200, text.encode(), "https://shop.example/p", [])
    metadata, artifact = sanitise(fetched, 2 * 1024 * 1024)
    assert all(value in artifact for value in (b"#g", b"#v", b"#o", b'"price":"120"'))
    assert b"private" not in artifact.lower()
    assert metadata["canonical_urls"] == ["https://shop.example/p"]
    assert safe_url("https://shop.example/p?token=private&selectedSize=50") == "https://shop.example/p?selectedSize=50"
    graph["@graph"][1]["offers"] = {"@id": "offer?token=private"}
    text = '<html><script type="application/ld+json">' + json.dumps(graph) + '</script></html>'
    fetched = Fetched("https://shop.example/p", 200, text.encode(), "https://shop.example/p", [])
    _, artifact = sanitise(fetched, 2 * 1024 * 1024)
    assert b"private" not in artifact.lower()


def test_private_storage_fails_closed(tmp_path):
    database = tmp_path / "test.sqlite"
    database.write_bytes(b"")
    if os.name != "posix":
        with pytest.raises(EvidenceError, match="verificar"):
            require_private_database(database)
    else:
        Path(database).chmod(0o644)
        with pytest.raises(EvidenceError, match="privados"):
            require_private_database(database)
        Path(database).chmod(0o600)
        require_private_database(database)


def test_capacity_checks_both_quota_and_disk(tmp_path, monkeypatch):
    from collections import namedtuple

    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("price_diagnostic.shutil.disk_usage", lambda path: usage(100, 91, 9))
    assert not has_capacity(tmp_path / "test.sqlite", 0, 5, 100)
    assert not has_capacity(tmp_path / "test.sqlite", 97, 5, 100)
    monkeypatch.setattr("price_diagnostic.shutil.disk_usage", lambda path: usage(100, 90, 10))
    assert has_capacity(tmp_path / "test.sqlite", 95, 5, 100)
