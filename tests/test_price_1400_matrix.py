import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "price_trial_manifest.json"
MATRIX = ROOT / "docs" / "Precios" / "100_EAN" / "MATRIZ_100_EAN_14_COMPETIDORES.csv"
SUMMARY = ROOT / "docs" / "Precios" / "100_EAN" / "MATRIZ_100_EAN_14_RESUMEN.json"


def test_exact_100_ean_x_14_unique_pair_matrix():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    eans = manifest["eans"]
    assert len(eans) == len(set(eans)) == 100
    assert all(ean.isascii() and ean.isdigit() and len(ean) == 13 for ean in eans)
    with MATRIX.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    pairs = {(row["ean"], row["competitor_id"]) for row in rows}
    assert len(rows) == len(pairs) == 1400
    assert {row["ean"] for row in rows} == set(eans)
    assert len({row["competitor_id"] for row in rows}) == 14
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["eans"] == 100
    assert summary["competitors"] == 14
    assert summary["pairs"] == summary["unique_pairs"] == 1400
    assert summary["comparable_current"] == 0
    assert summary["historical_only"] is True
