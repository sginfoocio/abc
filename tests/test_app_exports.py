import json
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