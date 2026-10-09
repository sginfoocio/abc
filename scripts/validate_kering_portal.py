from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kering_images import ConfigStore, ImageStore, data_root, process_ean
from kering_portal import KeringPortal


class MeasuredPortal(KeringPortal):
    def __init__(self, config):
        super().__init__(config)
        self.lookups = 0
        self.downloads = 0

    def fetch(self, ean, pending):
        self.lookups += 1
        return super().fetch(ean, pending)

    def _read_image(self, url):
        self.downloads += 1
        return super()._read_image(url)


def validate(config, ean, root):
    probe = MeasuredPortal(config).check_access()
    if not probe["ok"]:
        return {"access": probe, "passed": False}
    portal = MeasuredPortal(config)
    try:
        complete_store = ImageStore(root / "complete")
        first = process_ean(complete_store, portal, ean)
        before = (portal.lookups, portal.downloads)
        repeated = process_ean(complete_store, portal, ean)
        repeated_calls = (portal.lookups - before[0], portal.downloads - before[1])
        partial_store = ImageStore(root / "partial")
        for view, path in complete_store.valid_views(ean).items():
            if view in {"frontal", "perspectiva"}:
                partial_store.save_view(ean, view, path.read_bytes())
        before = (portal.lookups, portal.downloads)
        partial = process_ean(partial_store, portal, ean)
        partial_calls = (portal.lookups - before[0], portal.downloads - before[1])
        return {
            "access": probe, "ean": ean, "first": first, "repeated": repeated,
            "repeated_calls": repeated_calls, "partial_repository_retry": partial,
            "partial_calls": partial_calls,
            "passed": len(complete_store.valid_views(ean)) == 3
                and repeated_calls == (0, 0) and len(partial_store.valid_views(ean)) == 3
                and partial_calls == (1, 1),
        }
    finally:
        portal.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ean", required=True)
    parser.add_argument("--allow-network", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.allow_network:
        parser.error("La validacion real requiere --allow-network; no ejecutar desde CI")
    try:
        config = ConfigStore(data_root()).load()
        with tempfile.TemporaryDirectory(prefix="kering-real-validation-") as temporary:
            report = validate(config, arguments.ean, Path(temporary))
    except Exception:
        report = {"passed": False, "code": "validacion_real_fallida"}
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())