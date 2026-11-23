import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import app_exports
from provider_images import ProviderSettings, ProviderStore
from app_exports import (
    dataframe_to_excel_bytes,
    extract_clean_eans,
    generate_luxoptica_request_files,
    link_luxoptica_masterdata,
    normalize_result_export_schema,
    preview_luxoptica_request_products,
    split_report_warnings,
)


def test_masterdata_export_schema_maps_aliases_and_preserves_text() -> None:
    source = pd.DataFrame(
        [{"UPC": "000123", "Nombre de la marca": "Brand", "Código del modelo": "0RB-12", "PVP sugerido": "100"}]
    )

    result = normalize_result_export_schema(source)

    assert len(result.columns) == 18
    assert result.loc[0, "Barcode"] == "000123"
    assert result.loc[0, "Marca"] == "Brand"
    assert result.loc[0, "Modelo"] == "RB-12"
    assert result.loc[0, "PVP"] == "100"


def test_luxoptica_eans_are_cleaned_and_deduplicated() -> None:
    source = pd.DataFrame(
        {
            "Barcode": ["ES.123456789012", "123456789012", "0123456789012", "nan", ""],
            "Modelo": ["Model A", "Model B", "Model C", "", ""],
            "Color": ["01", "02", "03", "", ""],
        }
    )

    assert extract_clean_eans(source) == ["123456789012"]


def _catalog(monkeypatch, root, coverage):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_exports, "repository_root", lambda: root)
    monkeypatch.setattr(
        app_exports, "catalog_coverage",
        lambda codes, **_kwargs: {
            "catalog": {"status": "consultado"},
            "coverage": {code: coverage.get(code, {
                "label": "sin_registros", "basis": "archivos_verificados",
            }) for code in codes},
        },
    )


def test_request_files_keep_manifest_and_txt_batch_content(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "image-repository"
    _catalog(monkeypatch, image_root, {})
    source = pd.DataFrame(
        [
            {"Barcode": "ES.123456789012", "Modelo": "Model A", "Color": "01"},
            {"Barcode": "987654321098", "Modelo": "Model B", "Color": "02"},
        ]
    )

    files = generate_luxoptica_request_files(
        source, tmp_path / "lots", batch_size=1, request_root=tmp_path / "requests",
    )

    assert len(files) == 2
    assert files[0].read_text(encoding="utf-8").strip() == "123456789012"
    manifest = json.loads(files[0].with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["request_id"] == 1
    assert manifest["products"] == [{"ean": "123456789012", "modelo": "Model A", "color": "01"}]


def test_request_files_only_include_absent_eans_and_skip_partial_or_live(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    complete, partial, absent, pending = (
        "8056262672563", "123456789012", "987654321098", "4006381333931",
    )
    _catalog(monkeypatch, image_root, {
        complete: {"label": "completas", "basis": "archivos_verificados"},
        partial: {"label": "parciales", "basis": "archivos_verificados"},
    })
    ProviderStore(tmp_path / "requests").plan(
        ProviderSettings("luxottica"), [pending], mode="manual",
    )
    source = pd.DataFrame(
        [
            {"Barcode": complete, "Modelo": "Complete", "Color": "01"},
            {"Barcode": partial, "Modelo": "Partial", "Color": "02"},
            {"Barcode": absent, "Modelo": "Absent", "Color": "03"},
            {"Barcode": pending, "Modelo": "Pending", "Color": "04"},
        ]
    )

    preview = preview_luxoptica_request_products(
        source, request_root=tmp_path / "requests", image_root=image_root,
    )
    assert preview["candidates"] == [absent]
    assert [row["Estado"] for row in preview["rows"]] == [
        "Con imágenes", "Parcial", "Ausente", "Solicitud pendiente",
    ]

    files = generate_luxoptica_request_files(
        source, tmp_path / "lots", request_root=tmp_path / "requests", image_root=image_root,
    )

    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").strip() == absent
    store = ProviderStore(tmp_path / "requests")
    assert len(store.live_keys("luxottica")) == 2


@pytest.mark.parametrize("field", ["Marca", "Modelo", "Color", "Calibre"])
def test_contradictory_equivalent_codes_require_review(tmp_path, monkeypatch, field) -> None:
    image_root = tmp_path / "images"
    _catalog(monkeypatch, image_root, {})
    source = pd.DataFrame([
        {"Barcode": "123456789012", "Marca": "Brand", "Modelo": "Model A", "Color": "01", "Calibre": "50"},
        {"Barcode": "0123456789012", "Marca": "Brand", "Modelo": "Model A", "Color": "01", "Calibre": "50"},
    ])
    source.loc[1, field] = "different"

    preview = preview_luxoptica_request_products(
        source, request_root=tmp_path / "requests", image_root=image_root, output_dir=tmp_path / "lots",
    )

    assert preview["candidates"] == []
    assert preview["rows"][0]["Estado"] == "Revisión"
    assert generate_luxoptica_request_files(
        source, tmp_path / "lots", request_root=tmp_path / "requests", image_root=image_root,
    ) == []


def test_matching_equivalent_codes_remain_eligible(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    _catalog(monkeypatch, image_root, {})
    source = pd.DataFrame([
        {"Barcode": "123456789012", "Modelo": "Model A", "Color": "01"},
        {"Barcode": "0123456789012", "Modelo": " model a ", "Color": "01"},
    ])

    preview = preview_luxoptica_request_products(
        source, request_root=tmp_path / "requests", image_root=image_root, output_dir=tmp_path / "lots",
    )

    assert preview["candidates"] == ["123456789012"]


def test_order_evidence_links_to_masterdata_once_and_survives_restart(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    _catalog(monkeypatch, image_root, {})
    source = pd.DataFrame([{
        "Barcode": "123456789012", "Marca": "Brand", "Modelo": "Model A", "Color": "01", "Calibre": "50",
    }])
    files = generate_luxoptica_request_files(
        source, tmp_path / "lots", request_root=tmp_path / "requests",
        image_root=image_root, source_name="original.xlsx",
    )
    rid = json.loads(files[0].with_suffix(".manifest.json").read_text(encoding="utf-8"))["request_id"]
    item = {
        "ean": "0123456789012", "sku": "A", "order_id": 25, "order": "PO25", "supplier": "Luxottica",
        "date": "2026-11-22", "status": "confirmed",
        "lines": [{"line_id": 101, "product_id": 40, "ean": "0123456789012", "sku": "A-01",
                   "product": "Model A", "quantity": "2", "model": "Model A", "color": "01",
                   "caliber": "50", "barcode_conflict": False}],
    }

    def attach(_):
        store = ProviderStore(tmp_path / "requests")
        return store.plan(ProviderSettings("luxottica"), [item["ean"]], mode="manual",
                          order_evidence={item["ean"]: item}, reviewer="admin")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attach, range(2)))
    assert all(result["created"] == [] and result["linked_evidence"] == [item["ean"]] for result in results)
    store = ProviderStore(tmp_path / "requests")
    evidence = store.source_evidence(rid)
    assert len(evidence) == 2
    assert {row["source"] for row in evidence} == {"masterdata", "odoo"}
    assert next(row for row in evidence if row["source"] == "masterdata")["evidence"]["ean"] == "123456789012"
    order = next(row for row in evidence if row["source"] == "odoo")["evidence"]
    assert (order["ean"], order["order_id"], order["line_id"], order["product_id"]) == (
        "0123456789012", 25, 101, 40,
    )
    assert store.get(rid)["state"] == "ready"
    with closing(store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_existing_request_without_matching_identity_is_not_attributed(tmp_path) -> None:
    store = ProviderStore(tmp_path)
    settings = ProviderSettings("luxottica")
    rid, = store.plan(settings, ["123456789012"], mode="manual")["created"]
    item = {
        "ean": "123456789012", "order_id": 25, "order": "PO25", "supplier": "Luxottica",
        "date": "2026-11-22", "status": "confirmed",
        "lines": [{"line_id": 1, "product_id": 2, "ean": "123456789012", "sku": "A",
                   "model": "A", "color": "01", "caliber": "50", "barcode_conflict": False}],
    }
    result = store.plan(settings, [item["ean"]], mode="manual",
                        order_evidence={item["ean"]: item}, reviewer="admin")
    assert result["identity_review"] == [item["ean"]]
    assert store.source_evidence(rid) == []


def test_masterdata_can_be_explicitly_linked_to_existing_order(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    _catalog(monkeypatch, image_root, {})
    store = ProviderStore(tmp_path / "requests")
    item = {
        "ean": "0123456789012", "sku": "A", "order_id": 25, "order": "PO25", "supplier": "Luxottica",
        "date": "2026-11-22", "status": "confirmed",
        "lines": [{"line_id": 1, "product_id": 2, "ean": "0123456789012", "sku": "A",
                   "model": "Model A", "color": "01", "caliber": "50", "barcode_conflict": False}],
    }
    rid, = store.plan(ProviderSettings("luxottica"), [item["ean"]], mode="manual",
                      order_evidence={item["ean"]: item}, reviewer="admin")["created"]
    source = pd.DataFrame([{
        "Barcode": "123456789012", "Modelo": "Model A", "Color": "01", "Calibre": "50",
    }])
    kwargs = {"reviewer": "admin", "source_name": "original.xlsx", "request_root": tmp_path / "requests",
              "image_root": image_root, "output_dir": tmp_path / "lots"}
    preview_luxoptica_request_products(source, **{k: v for k, v in kwargs.items()
                                                 if k in {"request_root", "image_root", "output_dir"}})
    assert [row["source"] for row in store.source_evidence(rid)] == ["odoo"]
    assert link_luxoptica_masterdata(source, **kwargs)["linked_evidence"] == ["123456789012"]
    assert link_luxoptica_masterdata(source, **kwargs)["linked_evidence"] == ["123456789012"]
    assert len(ProviderStore(tmp_path / "requests").source_evidence(rid)) == 2
    assert store.get(rid)["state"] == "ready"


@pytest.mark.parametrize("field", ["model", "color", "caliber"])
def test_order_contradiction_does_not_link_existing_masterdata(tmp_path, monkeypatch, field) -> None:
    image_root = tmp_path / "images"
    _catalog(monkeypatch, image_root, {})
    source = pd.DataFrame([{"Barcode": "123456789012", "Modelo": "A", "Color": "01", "Calibre": "50"}])
    files = generate_luxoptica_request_files(
        source, tmp_path / "lots", request_root=tmp_path / "requests", image_root=image_root,
    )
    rid = json.loads(files[0].with_suffix(".manifest.json").read_text(encoding="utf-8"))["request_id"]
    line = {"line_id": 1, "product_id": 2, "ean": "123456789012", "sku": "A",
            "model": "A", "color": "01", "caliber": "50", "barcode_conflict": False}
    line[field] = "different"
    item = {"ean": line["ean"], "order_id": 25, "order": "PO25", "supplier": "Luxottica",
            "date": "2026-11-22", "status": "confirmed", "lines": [line]}
    store = ProviderStore(tmp_path / "requests")
    result = store.plan(ProviderSettings("luxottica"), [item["ean"]], mode="manual",
                        order_evidence={item["ean"]: item}, reviewer="admin")
    assert result["identity_review"] == [item["ean"]]
    assert [row["source"] for row in store.source_evidence(rid)] == ["masterdata"]


@pytest.mark.parametrize("change", ["barcode_conflict", "product_id", "sku", "ean"])
def test_order_lines_with_conflicting_identity_are_not_linked(tmp_path, change) -> None:
    store = ProviderStore(tmp_path)
    code = "123456789012"
    master = {"ean": code, "model": "A", "color": "01", "caliber": "50"}
    rid, = store.plan(ProviderSettings("luxottica"), [code], mode="manual",
                      masterdata_evidence={code: [master]})["created"]
    line = {"line_id": 1, "product_id": 2, "ean": code, "sku": "A",
            "model": "A", "color": "01", "caliber": "50", "barcode_conflict": False}
    other = {**line, "line_id": 2}
    other[change] = {"barcode_conflict": True, "product_id": 3, "sku": "B",
                     "ean": "8056597965903"}[change]
    item = {"ean": code, "order_id": 25, "order": "PO25", "supplier": "Luxottica",
            "date": "2026-11-22", "status": "confirmed", "lines": [line, other]}
    result = store.plan(ProviderSettings("luxottica"), [code], mode="manual",
                        order_evidence={code: item}, reviewer="admin")
    assert result["identity_review"] == [code]
    assert [row["source"] for row in store.source_evidence(rid)] == ["masterdata"]


def test_concurrent_plans_create_one_active_request(tmp_path) -> None:
    ProviderStore(tmp_path)
    code = "123456789012"

    def create(index):
        evidence = {code: [{"ean": code, "model": "A", "color": "01", "caliber": "50",
                            "source_file": f"file-{index}.xlsx"}]}
        return ProviderStore(tmp_path).plan(ProviderSettings("luxottica"), [code], mode="manual",
                                           masterdata_evidence=evidence)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sum(len(result["created"]) for result in results) == 1
    store = ProviderStore(tmp_path)
    assert len(store.list_requests("luxottica")) == 1
    sources = store.source_evidence(store.list_requests("luxottica")[0]["id"])
    assert {row["evidence"]["source_file"] for row in sources} == {"file-0.xlsx", "file-1.xlsx"}


def test_multiple_order_lines_and_orders_preserve_distinct_sources(tmp_path) -> None:
    store = ProviderStore(tmp_path)
    code = "123456789012"
    master = {"ean": code, "model": "A", "color": "01", "caliber": "50"}
    rid, = store.plan(ProviderSettings("luxottica"), [code], mode="manual",
                      masterdata_evidence={code: [master]})["created"]
    line = {"line_id": 1, "product_id": 2, "ean": code, "sku": "A", "model": "A",
            "color": "01", "caliber": "50", "barcode_conflict": False}
    item = {"ean": code, "order_id": 25, "order": "PO25", "supplier": "Luxottica",
            "date": "2026-11-22", "status": "confirmed",
            "lines": [line, {**line, "line_id": 2}]}
    for candidate in (item, {**item, "order_id": 26, "order": "PO26",
                             "lines": [{**line, "line_id": 3}]}, item):
        result = store.plan(ProviderSettings("luxottica"), [code], mode="manual",
                            order_evidence={code: candidate}, reviewer="admin")
        assert result["created"] == []
        assert result["linked_evidence"] == [code]
    evidence = ProviderStore(tmp_path).source_evidence(rid)
    assert {(row["evidence"]["order_id"], row["evidence"]["line_id"]) for row in evidence
            if row["source"] == "odoo"} == {(25, 1), (25, 2), (26, 3)}
    assert len(evidence) == 4
    assert store.get(rid)["state"] == "ready"


def test_retry_copies_only_selected_ean_evidence_without_changing_original(tmp_path) -> None:
    store = ProviderStore(tmp_path)
    settings = ProviderSettings("luxottica")
    first, second = "123456789012", "8056597965903"
    evidence = {code: [{"ean": code, "model": "A", "color": "01", "caliber": "50"}]
                for code in (first, second)}
    rid, = store.plan(settings, [first, second], mode="manual", masterdata_evidence=evidence)["created"]
    before = store.source_evidence(rid)
    with closing(store.connect()) as db:
        db.execute("UPDATE requests SET state='failed' WHERE id=?", (rid,))
        db.execute("UPDATE request_eans SET live=0 WHERE request_id=?", (rid,))
    retry = store.retry(settings, rid, manual_codes={first.zfill(14)}, reviewer="admin")
    assert store.source_evidence(rid) == before
    assert [row["evidence"]["ean"] for row in store.source_evidence(retry)] == [first]


def test_catalog_failure_and_unindexed_images_never_become_candidates(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    image_root.mkdir()
    ean = "8056262672563"
    source = pd.DataFrame([{"Barcode": ean, "Modelo": "Model", "Color": "01"}])
    monkeypatch.setattr(app_exports, "repository_root", lambda: image_root)
    monkeypatch.setattr(
        app_exports, "catalog_coverage",
        lambda *_args, **_kwargs: {"catalog": {"status": "inaccesible"}, "coverage": {}},
    )

    blocked = preview_luxoptica_request_products(source, request_root=tmp_path / "requests")
    assert blocked["candidates"] == []
    with pytest.raises(ValueError, match="catálogo no está disponible"):
        generate_luxoptica_request_files(
            source, tmp_path / "lots", request_root=tmp_path / "requests",
        )

    (image_root / ean).mkdir()
    (image_root / ean / "unindexed.jpg").write_bytes(b"not an image")
    _catalog(monkeypatch, image_root, {})
    unindexed = preview_luxoptica_request_products(source, request_root=tmp_path / "requests")
    assert unindexed["candidates"] == []
    assert unindexed["rows"][0]["Estado"] == "Revisión"


def test_unreadable_unindexed_images_block_request_preview(tmp_path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    ean = "8056262672563"
    folder = image_root / ean
    folder.mkdir(parents=True)
    source = pd.DataFrame([{"Barcode": ean, "Modelo": "Model", "Color": "01"}])
    _catalog(monkeypatch, image_root, {})
    real_scandir = app_exports.os.scandir

    def scandir(path):
        if Path(path) == folder:
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(app_exports.os, "scandir", scandir)

    with pytest.raises(ValueError, match="No se pudo verificar"):
        preview_luxoptica_request_products(
            source, request_root=tmp_path / "requests", image_root=image_root,
        )


def test_excel_export_and_warning_split_remain_data_only() -> None:
    workbook = dataframe_to_excel_bytes(pd.DataFrame([{"value": "text"}]))
    report = SimpleNamespace(
        discarded=1,
        discarded_accessories=1,
        discarded_invalid_product=0,
        leading_zero_real_loss_columns=0,
        invalid_yes_no_rows=0,
        unmatched_brand_names=["Unknown"],
        anomalous_values={"Forma": ["Odd"]},
    )

    blocking, medium, info = split_report_warnings(report)

    assert workbook.startswith(b"PK")
    assert len(blocking) == 1
    assert len(medium) == 2
    assert len(info) == 1