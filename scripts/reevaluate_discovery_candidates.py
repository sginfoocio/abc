"""Read-only report: how many stored discovery candidates the automatic identity rule would confirm.

The production database files are never opened with SQLite: the database and its WAL are copied byte for byte
into a temporary directory (checksums before and after must match, so a concurrent write aborts the copy), the
schema migration and the dry-run evaluation run on that copy only, and the copy is then deleted.
No HTTP request is made and nothing is confirmed. Use the «Reevaluar candidatos históricos» action in the
application (or ``PilotDiscovery.reevaluate(note=..., apply=True)``) to apply the result explicitly.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from price_comparator import PriceComparatorStore, resolve_database_path  # noqa: E402
from price_discovery import PilotDiscovery  # noqa: E402


def _digests(paths: list[Path]) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.exists()}


def _copy(source: Path, folder: Path, attempts: int = 3) -> Path:
    originals = [source, Path(str(source) + "-wal")]
    journal = Path(str(source) + "-journal")
    if journal.exists() and journal.stat().st_size:
        raise OSError("Hay un journal de rollback pendiente; repita el informe más tarde")
    for _ in range(attempts):
        before = _digests(originals)
        for path in originals:
            if path.exists():
                shutil.copyfile(path, folder / path.name)
        if _digests(originals) == before and _digests([folder / name for name in before]) == before:
            copy = folder / source.name
            with closing(sqlite3.connect(copy)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("La copia no supera integrity_check")
            return copy
        for name in before:
            (folder / name).unlink(missing_ok=True)
    raise OSError("La base de datos cambió durante la copia; repita el informe")


def report(source: Path) -> dict:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No existe la base de datos: {source}")
    with tempfile.TemporaryDirectory(prefix="reevaluation-") as folder:
        store = PriceComparatorStore(_copy(source, Path(folder)))
        frame = PilotDiscovery(store).reevaluate()
        names = {row["id"]: row["name"] for row in store.competitors().to_dict("records")}
    if frame.empty:
        return {"source": str(source), "evaluated": 0, "would_confirm": 0, "manual_review": 0,
                "by_competitor": {}, "manual_reasons": {}}
    frame["competitor"] = frame["competitor_id"].map(lambda cid: names.get(cid, cid))
    by_competitor = {name: group["result"].value_counts().to_dict() for name, group in frame.groupby("competitor")}
    reasons = Counter(reason.strip() for text in frame.loc[frame.result == "manual_review", "reasons"]
                      for reason in text.split(";") if reason.strip())
    return {"source": str(source), "evaluated": len(frame),
            "would_confirm": int((frame.result == "would_confirm").sum()),
            "manual_review": int((frame.result == "manual_review").sum()),
            "by_competitor": by_competitor, "manual_reasons": dict(reasons.most_common())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=None,
                        help="Ruta de price_comparator.sqlite3 (por defecto, la configurada para la aplicación)")
    args = parser.parse_args()
    try:
        result = report(args.database or resolve_database_path())
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, f"Informe no disponible: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
