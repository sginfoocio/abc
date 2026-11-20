"""Bounded, on-demand exports of current repository images (not historical copies)."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
from time import monotonic
from zipfile import ZipFile, ZIP_STORED
from typing import BinaryIO
from PIL import Image

from image_naming import sanitize_filename
from image_repository import ImageRecord, ImageRepository, collision_name, image_fingerprint
import image_library as library
from market_publication import (
    MARKETS, VIEW_NAMES, ReadOnlyKeringReviewReader, _output_bytes, create_publication_plan, read_export_market_ids,
)
from provider_images import ean_key
from repository_catalog_readonly import MAX_FILE_BYTES


MAX_EXPORT_BYTES = 256 * 1024 * 1024
MAX_EXPORT_FILES = 2000
REQUIRED_VIEWS = ("frontal", "lateral", "perspectiva")
MAX_REPOSITORY_EXPORT_BYTES = 64 * 1024 * 1024
MAX_REPOSITORY_EXPORT_SECONDS = 60
MAX_REPOSITORY_IMAGE_PIXELS = 25_000_000


@dataclass
class ExportPlan:
    files: list
    manifest: dict
    signature: str
    filename: str


def repository_signature(repository: ImageRepository, order: dict) -> str:
    entries = []
    for ean in dict.fromkeys(line["ean"] for line in order["lines"] if line["ean"]):
        for record in repository.records(ean):
            try:
                stat = record.path.stat()
                state = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except FileNotFoundError:
                state = None
            entries.append((record.id, record.checksum, record.view, record.market, record.name,
                            record.metadata, str(record.path), state))
    return hashlib.sha256(json.dumps({"order": order, "entries": entries}, sort_keys=True).encode()).hexdigest()


def order_export_plan(repository: ImageRepository, order: dict) -> ExportPlan:
    files = []
    products = []
    used = {}
    total = 0
    for ean in dict.fromkeys(line["ean"] for line in order["lines"] if line["ean"]):
        available = {}
        views = set()
        invalid = []
        for record in repository.records(ean):
            try:
                content = repository.verified_bytes(record)
                image_fingerprint(content)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                invalid.append(record.name)
                continue
            name = f"{ean}/{record.name}"
            if name in used and used[name] != record.checksum:
                name = f"{ean}/{collision_name(record.name, record.checksum)}"
                index = 0
                while name in used and used[name] != record.checksum:
                    index += 1
                    name = f"{ean}/{collision_name(record.name, record.checksum, index)}"
            if name not in used:
                total += len(content)
                if total > MAX_EXPORT_BYTES or len(files) >= MAX_EXPORT_FILES:
                    raise ValueError("Pedido demasiado grande para un ZIP (limite 256 MiB / 2000 fotos)")
                used[name] = record.checksum
                files.append((name, record))
            available.setdefault(name, []).append({
                "provider": record.provider, "origin": record.origin, "view": record.view,
                "market": record.market, "checksum": record.checksum,
                "date": record.date, "path": record.path.relative_to(repository.root).as_posix(),
                "view_detection": record.metadata.get("view_detection"),
                "view_review": record.metadata.get("view_review"),
            })
        # Distinct pixels and canonical views determine coverage, not market copies.
        views.update(repository.valid_views(ean))
        products.append({"ean": ean, "files": available, "missing_views": [
            view for view in REQUIRED_VIEWS if view not in views], "invalid_files": invalid})
    manifest = {"order": order["name"], "order_id": order["id"], "products": products,
                "lines_without_ean": sum(not line["ean"] for line in order["lines"]),
                "current_images": True, "partial": any(p["missing_views"] for p in products)
                or any(not line["ean"] for line in order["lines"])}
    signature = repository_signature(repository, order)
    return ExportPlan(files, manifest, signature, sanitize_filename(order["name"]) + ".zip")


@dataclass
class PreparedExport:
    stream: BinaryIO
    plan: ExportPlan


@contextmanager
def prepare_order_zip(repository: ImageRepository, order: dict, plan: ExportPlan | None = None):
    plan = plan or order_export_plan(repository, order)
    if plan.signature != repository_signature(repository, order):
        raise ValueError("Los archivos o el pedido cambiaron; vuelva a preparar el ZIP")
    if not plan.files:
        raise ValueError("El pedido no tiene fotos validas para exportar")
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as stream:
        with ZipFile(stream, "w", ZIP_STORED) as archive:
            for name, record in plan.files:
                with repository.lock(record.ean):
                    archive.writestr(name, repository.verified_bytes(record))
            archive.writestr("manifest.json", json.dumps(plan.manifest, ensure_ascii=False, indent=2))
        stream.seek(0)
        if plan.signature != repository_signature(repository, order):
            raise ValueError("Los archivos o el pedido cambiaron durante la preparacion")
        yield PreparedExport(stream, plan)


def _before_deadline(deadline: float) -> None:
    if monotonic() > deadline:
        raise TimeoutError("La generacion del ZIP supera 60 segundos; reduzca la seleccion.")


@dataclass
class _RepositoryExportReader:
    repository: ImageRepository
    selected: list[ImageRecord]
    deadline: float
    verified_pixels: dict[int, str] = field(default_factory=dict, init=False)

    @property
    def root(self) -> Path:
        return self.repository.root

    def records(self, ean: str | None = None) -> list[ImageRecord]:
        return [r for r in self.selected if ean is None or r.ean == ean]

    def verified_bytes(self, record: ImageRecord) -> bytes:
        _before_deadline(self.deadline)
        path = self.repository.resolve(record.path.relative_to(self.root).as_posix())
        if path != record.path:
            raise ValueError(f"{record.ean}: ruta original modificada.")
        with path.open("rb") as source:
            content = source.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError(f"{record.ean}/{record.name}: supera 30 MiB por imagen.")
        if hashlib.sha256(content).hexdigest() != record.checksum:
            raise ValueError(f"{record.ean}/{record.name}: checksum incorrecto.")
        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > MAX_REPOSITORY_IMAGE_PIXELS:
                raise ValueError(f"{record.ean}/{record.name}: supera 25 megapixeles.")
        pixels = image_fingerprint(content)
        if record.pixels and pixels != record.pixels:
            raise ValueError(f"{record.ean}/{record.name}: huella visual incorrecta.")
        self.verified_pixels[record.id] = pixels
        _before_deadline(self.deadline)
        return content


def _catalog_signature(records: list[ImageRecord]) -> str:
    return hashlib.sha256(json.dumps([
        (r.id, r.ean, r.checksum, r.pixels, str(r.path), r.provider, r.origin,
         r.view, r.market, r.name, r.date, r.metadata) for r in records
    ], sort_keys=True).encode()).hexdigest()


def _export_products(records: list[ImageRecord], eans: list[str]) -> list[dict]:
    result = library.search_eans(library.group_records(records), eans)
    if result["invalid"] or not result["rows"]:
        raise ValueError("Seleccione al menos un EAN/UPC valido.")
    products = []
    for row in result["rows"]:
        code, groups = row["code"], row["groups"]
        if not groups:
            raise ValueError(f"{code}: sin imagenes en el catalogo.")
        related = sorted({other for group in groups for other in group.related_eans
                          if ean_key(other) != ean_key(code)})
        if related:
            raise ValueError(f"{code}: coincidencias con otros EAN ({', '.join(related[:10])}); "
                             "revise la asociacion de modelo y color antes de exportar.")
        items = [record for group in groups for record in group.records]
        identity = library.variant_identity(items)
        if len(identity["models"]) > 1 or len(identity["colors"]) > 1:
            raise ValueError(f"{code}: modelo o color contradictorio; requiere revision.")
        photos: dict[str, ImageRecord] = {}
        for record in sorted(items, key=lambda r: (bool(r.market), library.record_sort_key(r))):
            photos.setdefault(record.checksum, record)
        products.append({"ean": code, "records": items, "photos": list(photos.values()), "identity": identity})
    return products


def _original_export_entries(products: list[dict]) -> list[tuple[str, ImageRecord, str]]:
    entries = []
    used = set()
    for product in products:
        for record in product["photos"]:
            filename = sanitize_filename(record.name).rstrip(". ")
            if not filename or any(ord(character) < 32 for character in filename):
                raise ValueError(f"{record.ean}: nombre original no valido.")
            name = f"{product['ean']}/{filename}"
            index = 0
            while name.casefold() in used:
                name = f"{product['ean']}/{collision_name(filename, record.checksum, index)}"
                index += 1
            used.add(name.casefold())
            entries.append((name, record, record.checksum))
    return entries


def _commercial_export_entries(reader, products, export_format, engine_factory, temporary, *, expected_variants=None):
    if engine_factory is None:
        raise ValueError("La exportacion especial necesita conexion Odoo para verificar los IDs externos.")
    eans = [product["ean"] for product in products]
    if expected_variants is None:
        ids = read_export_market_ids(engine_factory(), eans, export_format)
    else:
        ids = read_export_market_ids(engine_factory(), eans, export_format, expected_variants=expected_variants)
        if len({item[export_format] for item in ids.values()}) != len(ids):
            raise ValueError("Varias variantes comparten un identificador comercial.")
    # Publication reads the literal catalog EAN; the search may have used its UPC/EAN equivalent.
    literal_ids = {record.ean: ids[product["ean"]]
                   for product in products for record in product["records"]}
    plan = create_publication_plan(
        reader, literal_ids, Path(temporary) / "publication",
        kering_reviews=ReadOnlyKeringReviewReader(),
    )
    entries = []
    included = set()
    views_by_photo = {}
    requested = {ean_key(ean): ean for ean in eans}
    for entry in plan.entries:
        key = (ean_key(entry.ean), entry.source_checksum)
        if entry.market != export_format:
            continue
        code = requested[ean_key(entry.ean)]
        if expected_variants is not None:
            photo = (key[0], reader.verified_pixels[entry.source_id])
            if views_by_photo.setdefault(photo, entry.view) != entry.view:
                raise ValueError(f"{code}: la misma fotografía acredita vistas incompatibles; requiere revisión.")
        if key in included:
            continue
        name = f"{export_format}/{code}/{Path(entry.relative_path).name}"
        if any(previous.casefold() == name.casefold() for previous, _, _ in entries):
            raise ValueError(f"{code}: colision de nombres comerciales; requiere revision.")
        included.add(key)
        entries.append((name, entry.record, entry.output_checksum))
    excluded = []
    for product in products:
        code = product["ean"]
        for record in product["photos"]:
            if (ean_key(code), record.checksum) not in included:
                reasons = sorted({item["reason"] for item in plan.skipped
                                  if ean_key(item["ean"]) == ean_key(code)
                                  and item.get("market") in {None, export_format}
                                  and item.get("source") in {None, record.name}})
                excluded.append({"ean": code, "source": record.name, "checksum": record.checksum,
                                 "reasons": reasons or ["sin_vista_original_acreditada"]})
        if expected_variants is None and not any(ean_key(record.ean) == ean_key(code) for _, record, _ in entries):
            raise ValueError(f"{code}: ninguna fotografia publicable en {export_format}; "
                             "revise vistas acreditadas, formatos e historial Kering. Utilice Original.")
    return entries, excluded


@contextmanager
def prepare_repository_zip(
    repository: ImageRepository, eans: list[str], *, role: str | None,
    export_format: str = "Original", engine_factory=None, expected_variants=None,
    preview_only=False, expected_preview=None,
):
    """Bounded local ZIP, independent of orders and without publishing or changing originals."""
    if role not in {"admin", "masterdata"}:
        raise PermissionError("La descarga requiere acceso a la biblioteca de imagenes.")
    if export_format not in {"Original", *MARKETS}:
        raise ValueError("Formato de exportacion no admitido.")
    if expected_variants is not None and (len(eans) > 200 or set(eans) != set(expected_variants)):
        raise ValueError("Seleccione hasta 200 variantes identificadas por lote.")
    deadline = monotonic() + MAX_REPOSITORY_EXPORT_SECONDS

    def catalog():
        return repository.catalog_records(codes=eans) if expected_variants is not None else repository.catalog_records()

    snapshot = catalog()
    signature = _catalog_signature(snapshot)
    products = _export_products(snapshot, eans)
    photos = [record for product in products for record in product["photos"]]
    if len(photos) > MAX_EXPORT_FILES:
        raise ValueError("La seleccion supera 2000 fotografias unicas; reduzca el listado.")
    reader = _RepositoryExportReader(repository, [r for p in products for r in p["records"]], deadline)
    total = 0
    for record in photos:
        _before_deadline(deadline)
        path = repository.resolve(record.path.relative_to(repository.root).as_posix())
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"{record.ean}/{record.name}: supera 30 MiB por imagen.")
        total += size
        if total > MAX_REPOSITORY_EXPORT_BYTES:
            raise ValueError("La seleccion supera 64 MiB; reduzca el listado.")
    with tempfile.TemporaryDirectory(prefix="repository-export-") as temporary:
        entries, excluded = (
            (_original_export_entries(products), []) if export_format == "Original" else
            _commercial_export_entries(reader, products, export_format, engine_factory, temporary,
                                       expected_variants=expected_variants)
        )
        if expected_variants is not None:
            if export_format == "Original" and preview_only:
                for _, record, _ in entries:
                    reader.verified_bytes(record)
            literal_codes = {ean_key(ean): ean for ean in eans}
            actual_views = {ean: set() for ean in eans}
            for _, record, _ in entries:
                actual_views[literal_codes[ean_key(record.ean)]].add(record.view)
            preview = {
                "signature": signature, "format": export_format, "eans": eans,
                "files": [{"path": name, "ean": literal_codes[ean_key(record.ean)],
                           "view": record.view, "source": record.name, "checksum": checksum}
                          for name, record, checksum in entries],
                "excluded": excluded,
                "missing": {
                    ean: sorted(set(VIEW_NAMES) - views) for ean, views in actual_views.items()
                },
            }
            if preview_only:
                _before_deadline(deadline)
                yield preview
                return
            if expected_preview != preview:
                raise ValueError("Cambió el catálogo o los identificadores. Prepare otra vista previa.")
            if export_format != "Original" and any(preview["missing"].values()):
                raise ValueError("Faltan vistas acreditadas para el ZIP comercial.")
        manifest = {"format": export_format, "catalog_signature": signature, "excluded": excluded,
                    "products": [{"ean": p["ean"], "models": list(p["identity"]["models"]),
                                  "colors": list(p["identity"]["colors"])} for p in products], "files": []}
        by_key = {ean_key(p["ean"]): p for p in products}
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b", dir=temporary) as stream:
            with ZipFile(stream, "w", ZIP_STORED) as archive:
                for name, record, expected in entries:
                    content = reader.verified_bytes(record)
                    if export_format != "Original":
                        content, _ = _output_bytes(content, export_format)
                    checksum = hashlib.sha256(content).hexdigest()
                    if checksum != expected:
                        raise ValueError(f"{record.ean}: la imagen cambio durante la preparacion.")
                    if stream.tell() + len(content) > MAX_REPOSITORY_EXPORT_BYTES:
                        raise ValueError("El ZIP supera 64 MiB; reduzca el listado.")
                    archive.writestr(name, content)
                    manifest["files"].append({
                        "path": name, "ean": by_key[ean_key(record.ean)]["ean"],
                        "source_checksum": record.checksum, "output_checksum": checksum,
                        "representations": [
                            {"id": r.id, "ean": r.ean, "provider": r.provider, "origin": r.origin,
                             "view": r.view, "market": r.market, "name": r.name, "date": r.date}
                            for r in by_key[ean_key(record.ean)]["records"] if r.checksum == record.checksum
                        ],
                    })
                payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                if stream.tell() + len(payload) > MAX_REPOSITORY_EXPORT_BYTES:
                    raise ValueError("El ZIP y su trazabilidad superan 64 MiB; reduzca el listado.")
                archive.writestr("manifest.json", payload)
            if stream.tell() > MAX_REPOSITORY_EXPORT_BYTES:
                raise ValueError("El ZIP supera 64 MiB; reduzca el listado.")
            if signature != _catalog_signature(catalog()):
                raise ValueError("El catalogo cambio; vuelva a preparar el ZIP.")
            _before_deadline(deadline)
            stream.seek(0)
            yield PreparedExport(stream, ExportPlan(
                [(name, record) for name, record, _ in entries], manifest, signature,
                f"imagenes-{export_format.lower()}.zip",
            ))
