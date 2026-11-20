from hashlib import sha256
from io import BytesIO
import json
from unittest.mock import MagicMock, Mock
from zipfile import ZipFile

from openpyxl import Workbook
from PIL import Image
import pytest
from sqlalchemy import create_engine, event, text

import bulk_image_search as bulk
import image_exports as exports
import image_library as library
from image_repository import ImageRepository
from market_publication import ReadOnlyImageRepository, read_export_market_ids
from provider_images import parse_ean_text
from provider_support import EANS, photo

UPC = "036000291452"
EAN = "0036000291452"


@pytest.fixture
def repository(tmp_path):
    return ImageRepository(tmp_path / "images", state=tmp_path / "state", backups=tmp_path / "backups")


@pytest.fixture
def engine():
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.execute(text("CREATE TABLE product_product(id INTEGER, barcode TEXT)"))
        connection.execute(text("CREATE TABLE diagonal_product_website"
                                "(product_id INTEGER, siteweb_id INTEGER, product_website_id TEXT)"))
        for index, ean in enumerate([EAN, *EANS]):
            connection.execute(text("INSERT INTO product_product VALUES (:id, :ean)"), {"id": index, "ean": ean})
            connection.execute(text("INSERT INTO diagonal_product_website VALUES (:id, 3, :ff), (:id, 2, :mi)"),
                               {"id": index, "ff": f"FF-{index}", "mi": f"00{index}"})
    yield database
    database.dispose()


def save(repository, ean=UPC, view="frontal", seed=1, **kwargs):
    suffix = {"frontal": "noshad__fr", "perspectiva": "noshad__qt", "lateral": "shad__lt"}.get(view, view)
    return repository.save(ean, kwargs.pop("name", f"MODEL__COLOR__{suffix}.png"),
                           kwargs.pop("content", photo(seed)), provider=kwargs.pop("provider", "Luxoptica"),
                           origin=kwargs.pop("origin", "synthetic"), view=view, **kwargs)[0]


def archive(repository, eans, **kwargs):
    with exports.prepare_repository_zip(repository, eans, role="admin", **kwargs) as result:
        payload = result.stream.read()
        manifest = result.plan.manifest
    with ZipFile(BytesIO(payload)) as zipped:
        assert zipped.testzip() is None
        assert json.loads(zipped.read("manifest.json")) == manifest
        files = {name: zipped.read(name) for name in zipped.namelist() if name != "manifest.json"}
        assert len(zipped.namelist()) == len(set(zipped.namelist()))
    return files, manifest


def workbook(rows, formatted=None):
    document = Workbook()
    for row in rows:
        document.active.append(row)
    if formatted:
        document.active[formatted[0]].number_format = formatted[1]
    stream = BytesIO()
    document.save(stream)
    document.close()
    return stream.getvalue()


def test_search_single_multiple_duplicates_missing_and_leading_zero_equivalence(repository):
    save(repository)
    save(repository, EANS[0], seed=2, metadata={"modelo": "MODEL", "color": "002"})
    groups = library.group_records(repository.catalog_records())
    assert library.search_eans(groups, [EAN])["rows"][0]["groups"][0].ean == UPC
    result = library.search_eans(groups, parse_ean_text(f"{EAN}, {UPC} {EAN}\n{EANS[0]};{EANS[1]} 123"))
    assert result["duplicates"] == 2 and result["invalid"] == ["123"]
    assert [row["code"] for row in result["rows"]] == [EAN, EANS[0], EANS[1]]
    assert [len(row["groups"]) for row in result["rows"]] == [1, 1, 0]
    assert result["rows"][1]["groups"][0].records[0].metadata["color"] == "002"
    assert library.search_eans(groups, [EAN] * 500)["duplicates"] == 499
    with pytest.raises(ValueError, match="500"):
        library.search_eans(groups, [EAN] * 501)


@pytest.mark.parametrize("provider", ["Luxoptica", "Kering", "Marcolin", "Safilo", "NuORDER"])
def test_original_download_is_local_for_every_provider(repository, provider):
    record = save(repository, provider=provider)
    factory = Mock(side_effect=AssertionError("No external lookup"))
    files, manifest = archive(repository, [EAN], engine_factory=factory)
    assert list(files.values()) == [record.path.read_bytes()]
    assert manifest["files"][0]["representations"][0]["provider"] == provider
    factory.assert_not_called()


@pytest.mark.parametrize("kind", ["Original", "Farfetch", "Miinto"])
def test_contextual_preview_and_zip_reuse_shared_formats(repository, engine, monkeypatch, kind):
    monkeypatch.setenv("IMAGE_REPOSITORY_STATE_ROOT", str(repository.state_root))
    for seed, view in enumerate(("frontal", "perspectiva", "lateral", "lifestyle", "unknown"), 1):
        save(repository, view=view, seed=seed)
    readonly = ReadOnlyImageRepository(repository.root)
    factory = Mock(return_value=engine)
    options = dict(role="admin", export_format=kind, engine_factory=factory, expected_variants={EAN: 0})
    before = {path: path.read_bytes() for root in (repository.root, repository.state_root)
              for path in root.rglob("*") if path.is_file()}
    with exports.prepare_repository_zip(readonly, [EAN], preview_only=True, **options) as plan:
        assert plan["missing"] == {EAN: []}
        assert len(plan["files"]) == (5 if kind == "Original" else 3)
        assert len(plan["excluded"]) == (0 if kind == "Original" else 2)
    with exports.prepare_repository_zip(readonly, [EAN], expected_preview=plan, **options) as prepared:
        with ZipFile(prepared.stream) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert {item["ean"] for item in manifest["files"]} == {EAN}
            for item in manifest["files"]:
                assert sha256(archive.read(item["path"])).hexdigest() == item["output_checksum"]
    assert {path: path.read_bytes() for root in (repository.root, repository.state_root)
            for path in root.rglob("*") if path.is_file()} == before
    assert factory.call_count == (0 if kind == "Original" else 2)


def test_contextual_commercial_preview_blocks_incomplete_or_wrong_variant(repository, engine, monkeypatch):
    monkeypatch.setenv("IMAGE_REPOSITORY_STATE_ROOT", str(repository.state_root))
    save(repository)
    save(repository, view="lifestyle", seed=2)
    readonly = ReadOnlyImageRepository(repository.root)
    options = dict(role="admin", export_format="Farfetch", engine_factory=lambda: engine)
    with exports.prepare_repository_zip(readonly, [EAN], preview_only=True,
                                        expected_variants={EAN: 0}, **options) as plan:
        assert plan["missing"][EAN] == ["lateral", "perspectiva"]
    with pytest.raises(ValueError, match="Faltan vistas acreditadas"):
        with exports.prepare_repository_zip(readonly, [EAN], expected_preview=plan,
                                            expected_variants={EAN: 0}, **options):
            pytest.fail("No debe existir un ZIP comercial parcial")
    with pytest.raises(ValueError, match="variante Odoo"):
        with exports.prepare_repository_zip(readonly, [EAN], preview_only=True,
                                            expected_variants={EAN: 1}, **options):
            pytest.fail("No debe admitir otra variante")


def test_scoped_preview_checks_other_eans_without_scanning_files(repository, monkeypatch):
    monkeypatch.setenv("IMAGE_REPOSITORY_STATE_ROOT", str(repository.state_root))
    record = save(repository)
    save(repository, ean=EANS[0], content=record.path.read_bytes())
    with pytest.raises(ValueError, match="coincidencias con otros EAN"):
        with exports.prepare_repository_zip(ReadOnlyImageRepository(repository.root), [EAN], role="admin",
                                            preview_only=True, expected_variants={EAN: 0}):
            pytest.fail("EAN ajeno comparte los píxeles")


def test_contextual_zip_never_uses_changed_or_corrupt_original(repository, monkeypatch):
    monkeypatch.setenv("IMAGE_REPOSITORY_STATE_ROOT", str(repository.state_root))
    record = save(repository)
    readonly = ReadOnlyImageRepository(repository.root)
    options = dict(role="admin", expected_variants={EAN: 0})
    with exports.prepare_repository_zip(readonly, [EAN], preview_only=True, **options) as plan:
        pass
    content = record.path.read_bytes()
    record.path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum incorrecto"):
        with exports.prepare_repository_zip(readonly, [EAN], expected_preview=plan, **options):
            pytest.fail("No debe existir un ZIP con un original corrupto")
    record.path.write_bytes(content)
    save(repository, view="detalle", seed=3)
    with pytest.raises(ValueError, match="Cambió el catálogo"):
        with exports.prepare_repository_zip(readonly, [EAN], expected_preview=plan, **options):
            pytest.fail("No debe existir un ZIP distinto de la vista previa")


def test_control_characters_in_original_names_block_archive(repository):
    record = save(repository)
    save(repository, name="A\nbad.png", content=record.path.read_bytes(), origin="invalid-alias")
    with pytest.raises(ValueError, match="nombre original"):
        archive(repository, [EAN])


def test_uploads_reuse_text_csv_and_preserve_excel_text_and_displayed_zeros():
    assert bulk.parse_ean_upload(f"EAN;nombre\n{EAN};x\n".encode(), "list.csv") == [EAN]
    assert bulk.parse_ean_upload(f'"nombre";"UPC"\n"texto; con separador";"{UPC}"\n'.encode(), "list.csv") == [UPC]
    assert bulk.parse_ean_upload(f"{EAN} {EANS[0]}\n".encode(), "list.txt") == [EAN, EANS[0]]
    data = workbook([["EAN", "Nombre"], [EAN, "x"], [int(UPC), "y"]], ("A3", "0000000000000"))
    assert bulk.parse_ean_upload(data, "list.xlsx") == [EAN, EAN]
    assert bulk.parse_ean_upload(workbook([[EAN], [EANS[0]]]), "list.xlsx") == [EAN, EANS[0]]


@pytest.mark.parametrize("data,name,reason", [
    (b"x" * (1024 * 1024 + 1), "list.txt", "1 MiB"),
    (b"not Excel", "list.xlsx", "valido"),
    (b"\xff", "list.csv", "utf-8"),
    (b"ean", "list.xls", ".xlsx"),
    (workbook([["EAN"], ['="0036000291452"']]), "list.xlsx", "formula"),
    (workbook([["EAN"], [1.5]]), "list.xlsx", "entero"),
    (workbook([["EAN"]] + [[EAN]] * 501), "list.xlsx", "500"),
], ids=["large-upload", "not-excel", "not-utf8", "legacy-xls", "formula", "decimal", "too-many-rows"])
def test_bad_uploads_fail_explicitly(data, name, reason):
    with pytest.raises((ValueError, UnicodeError), match=reason):
        bulk.parse_ean_upload(data, name)


@pytest.mark.parametrize("representations", [16, 40])
def test_original_zip_collapses_representations_not_views_or_originals(repository, representations):
    before = []
    for index in range(8):
        original = save(repository, view=("frontal", "perspectiva", "lateral", "detalle", "lifestyle", "unknown",
                                         "frontal", "detalle")[index],
                        seed=index + 1, name=f"original-{index}.png")
        before.append((original.path, original.path.read_bytes()))
        for alias in range(representations // 8 - 1):
            save(repository, view=original.view, content=original.path.read_bytes(),
                 name=f"alias-{index}-{alias}.png", origin=f"alias-{alias}", market="Farfetch")
    engine_factory = Mock(side_effect=AssertionError("Original must not query Odoo"))
    catalog = repository.catalog_records()
    files, manifest = archive(repository, [EAN, UPC, EAN], engine_factory=engine_factory)
    assert len(files) == 8 and all(name.startswith(f"{EAN}/original-") for name in files)
    assert {sha256(value).hexdigest() for value in files.values()} == {r.checksum for r in catalog}
    assert sum(len(item["representations"]) for item in manifest["files"]) == representations
    assert {r["view"] for item in manifest["files"] for r in item["representations"]} >= {
        "frontal", "detalle", "lifestyle", "unknown",
    }
    assert repository.catalog_records() == catalog
    assert all(path.read_bytes() == value for path, value in before)
    engine_factory.assert_not_called()


def test_multi_zip_separates_same_model_different_colors_and_name_collisions(repository):
    save(repository, metadata={"modelo": "MODEL", "color": "001"}, name="same.png")
    save(repository, seed=2, view="detalle", name="SAME.png", metadata={"modelo": "MODEL", "color": "001"})
    save(repository, EANS[0], seed=3, name="same.png", metadata={"modelo": "MODEL", "color": "002"})
    files, manifest = archive(repository, [EAN, EANS[0]])
    assert len(files) == 3 and len({name.casefold() for name in files}) == 3
    assert len([name for name in files if name.startswith(EAN + "/")]) == 2
    assert len([name for name in files if name.startswith(EANS[0] + "/")]) == 1
    assert {p["ean"]: p["colors"] for p in manifest["products"]} == {EAN: ["001"], EANS[0]: ["002"]}


@pytest.mark.parametrize("export_format,extension", [("Farfetch", "png"), ("Miinto", "jpeg")])
def test_special_multi_zip_uses_existing_ids_views_bytes_and_explicit_exclusions(repository, engine,
                                                                              export_format, extension):
    for index, ean in enumerate([UPC, EANS[0]]):
        for offset, view in enumerate(("frontal", "perspectiva", "lateral", "detalle", "lifestyle")):
            save(repository, ean, view, index * 10 + offset + 1)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, s, p, co, ex: statements.append(s))
    records = repository.catalog_records()
    files, manifest = archive(repository, [EAN, EANS[0]], export_format=export_format, engine_factory=lambda: engine)
    for index, ean in enumerate([EAN, EANS[0]]):
        identifier = f"FF-{index}" if export_format == "Farfetch" else f"00{index}"
        assert {f"{export_format}/{ean}/{identifier}_V{view}.{extension}" for view in (1, 2, 3)} <= files.keys()
    assert len(files) == 6 and len(manifest["excluded"]) == 4
    assert all("vista_no_publicable" in item["reasons"] for item in manifest["excluded"])
    assert all(Image.open(BytesIO(content)).format == "PNG" for content in files.values())
    assert all(Image.open(BytesIO(content)).size == (640, 640) for content in files.values())
    assert all(sha256(content).hexdigest() in {r.checksum for r in records} for content in files.values())
    assert repository.catalog_records() == records
    assert len(statements) == 1 and statements[0].lstrip().startswith("SELECT")


@pytest.mark.parametrize("change,reason", [
    ("DELETE FROM diagonal_product_website WHERE siteweb_id=3", "ausente"),
    ("INSERT INTO diagonal_product_website VALUES (0, 3, 'another-id')", "ambiguo"),
    ("INSERT INTO product_product VALUES (99, '036000291452')", "varias variantes"),
    ("UPDATE diagonal_product_website SET product_website_id='../outside' WHERE siteweb_id=3", "invalido"),
])
def test_market_ids_missing_ambiguous_and_unsafe_never_fall_back_to_ean(engine, change, reason):
    with engine.begin() as connection:
        connection.execute(text(change))
    with pytest.raises(ValueError, match=reason):
        read_export_market_ids(engine, [EAN], "Farfetch")


def test_special_zip_conversion_matches_publication_without_losing_pixels_or_originals(repository, engine):
    png = save(repository)
    originals = [png]
    for seed, view, suffix, encoding in [(30, "perspectiva", "noshad__qt", "JPEG"), (60, "lateral", "shad__lt", "WEBP")]:
        stream = BytesIO()
        Image.new("RGB", (800, 650), (seed, 90, 50)).save(stream, encoding)
        originals.append(save(repository, view=view, content=stream.getvalue(),
                              name=f"MODEL__COLOR__{suffix}.{encoding.lower()}"))
    before = {record.path: record.path.read_bytes() for record in originals}
    farfetch, _ = archive(repository, [EAN], export_format="Farfetch", engine_factory=lambda: engine)
    assert len(farfetch) == 3
    for record, view in zip(originals, (1, 2, 3)):
        output = farfetch[f"Farfetch/{EAN}/FF-0_V{view}.png"]
        with Image.open(BytesIO(output)) as image, Image.open(record.path) as source:
            assert image.format == "PNG" and image.size == source.size
            assert image.convert("RGB").tobytes() == source.convert("RGB").tobytes()
    miinto, manifest = archive(repository, [EAN], export_format="Miinto", engine_factory=lambda: engine)
    assert len(miinto) == 2 and len(manifest["excluded"]) == 1
    assert manifest["excluded"][0]["reasons"] == ["formato_no_admitido"]
    assert miinto[f"Miinto/{EAN}/000_V1.jpeg"] == before[png.path]
    assert miinto[f"Miinto/{EAN}/000_V2.jpeg"] == before[originals[1].path]
    assert all(path.read_bytes() == value for path, value in before.items())


def test_read_only_market_lookup_rolls_back_and_sets_postgresql_guards():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.engine.dialect.name = "postgresql"
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"product_id": 1, "ean": EAN, "siteweb_id": 3, "product_website_id": "0001"},
    ]
    assert read_export_market_ids(engine, [EAN], "Farfetch") == {EAN: {"Farfetch": "0001"}}
    statements = [str(call.args[0]).strip() for call in connection.execute.call_args_list]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert statements[1].startswith("SET LOCAL statement_timeout = ")
    assert statements[2].startswith("SELECT")
    connection.begin.return_value.rollback.assert_called_once()


@pytest.mark.parametrize("kind", ["checksum", "pixels", "identity"])
def test_suspicious_variant_associations_block_even_if_other_ean_not_selected(repository, kind):
    first = save(repository, metadata={"modelo": "MODEL", "color": "001"})
    if kind == "identity":
        save(repository, seed=3, view="lateral", metadata={"modelo": "MODEL", "color": "002"})
    else:
        content = first.path.read_bytes()
        if kind == "pixels":
            stream = BytesIO()
            Image.open(BytesIO(content)).save(stream, "PNG", compress_level=0)
            content = stream.getvalue()
            assert sha256(content).hexdigest() != first.checksum
        save(repository, EANS[0], content=content, metadata={"modelo": "MODEL", "color": "002"})
    with pytest.raises(ValueError, match="revision|revise"):
        archive(repository, [EAN])


@pytest.mark.parametrize("kind,reason", [
    ("missing", "No such file|cannot find|no puede encontrar|specified"),
    ("checksum", "checksum"),
    ("bytes", "64 MiB"),
    ("file", "30 MiB"),
    ("pixels", "25 megapixeles"),
    ("count", "2000"),
    ("time", "60 segundos"),
    ("catalog", "catalogo cambio"),
])
def test_archive_errors_and_limits_do_not_return_partial_zips(repository, monkeypatch, kind, reason):
    record = save(repository)
    if kind == "missing":
        record.path.unlink()
    elif kind == "checksum":
        record.path.write_bytes(photo(99))
    elif kind == "bytes":
        monkeypatch.setattr(exports, "MAX_REPOSITORY_EXPORT_BYTES", 1)
    elif kind == "file":
        monkeypatch.setattr(exports, "MAX_FILE_BYTES", 1)
    elif kind == "pixels":
        monkeypatch.setattr(exports, "MAX_REPOSITORY_IMAGE_PIXELS", 1)
    elif kind == "count":
        monkeypatch.setattr(exports, "MAX_EXPORT_FILES", 0)
    elif kind == "time":
        clock = iter([0, 61])
        monkeypatch.setattr(exports, "monotonic", lambda: next(clock))
    elif kind == "catalog":
        original = repository.catalog_records
        calls = 0

        def changed():
            nonlocal calls
            calls += 1
            return original() if calls == 1 else []

        monkeypatch.setattr(repository, "catalog_records", changed)
    with pytest.raises((ValueError, OSError, TimeoutError), match=reason):
        archive(repository, [EAN])


def test_permissions_missing_ean_and_legacy_download_are_preserved(repository):
    record = save(repository)
    save(repository, name="alias.png", content=record.path.read_bytes(), market="Miinto")
    with pytest.raises(PermissionError):
        with exports.prepare_repository_zip(repository, [EAN], role="viewer"):
            pytest.fail("Unauthorized archive")
    with pytest.raises(ValueError, match="sin imagenes"):
        archive(repository, [EANS[0]])
    with ZipFile(BytesIO(repository.zip_eans_for_user([UPC], role="admin"))) as legacy:
        assert len(legacy.namelist()) == 2
    assert len(archive(repository, [EAN])[0]) == 1
