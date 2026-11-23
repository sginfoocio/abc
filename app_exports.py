from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import stat

import pandas as pd

from image_repository import repository_root
from provider_images import ProviderSettings, ProviderStore, ean_key, ean_valid, normalize_eans
from provider_order_policy import provider_data_root
from repository_catalog_readonly import catalog_coverage, code_variants


def load_masterdata_file(file_source) -> pd.DataFrame:
    """Load a MASTERDATA workbook while preserving identifiers as text."""
    dataframe = pd.read_excel(file_source, dtype=str).fillna("")
    dataframe.columns = [str(column).strip() for column in dataframe.columns]
    for column in dataframe.columns:
        dataframe[column] = dataframe[column].astype(str).str.strip()
    return dataframe


def dataframe_to_excel_bytes(dataframe: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to an in-memory Excel workbook."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False)
    return buffer.getvalue()


def normalize_result_export_schema(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Ensure the canonical 18-column MASTERDATA export schema."""
    target_columns = [
        "Marca",
        "Colección",
        "Modelo",
        "Color",
        "Calibre",
        "Ancho Puente",
        "Longitud Varilla",
        "Género",
        "Color Frontal",
        "Color Lente",
        "Forma",
        "Material Principal",
        "Fotocromático",
        "Polarizado",
        "PVO",
        "PVP",
        "Barcode",
        "Categoría",
    ]
    alias_map = {
        "Barcode": ["Barcode", "UPC"],
        "Marca": ["Marca", "Nombre de la marca"],
        "Modelo": ["Modelo", "Código del modelo"],
        "Ancho Puente": ["Ancho Puente", "Dimensión del puente"],
        "Longitud Varilla": ["Longitud Varilla", "Largo de varilla"],
        "Color Frontal": ["Color Frontal", "Color del frontal"],
        "Color Lente": ["Color Lente", "Color de las lentes"],
        "Material Principal": ["Material Principal", "Material del frente"],
        "PVP": ["PVP", "PVP sugerido"],
    }

    normalized = dataframe.copy()
    for target, aliases in alias_map.items():
        if target in normalized.columns:
            continue
        source = next((name for name in aliases if name in normalized.columns), None)
        if source:
            normalized[target] = normalized[source]

    for column in target_columns:
        if column not in normalized.columns:
            normalized[column] = ""

    normalized["Modelo"] = normalized["Modelo"].map(lambda value: str(value).strip())
    normalized["Modelo"] = normalized["Modelo"].map(
        lambda value: value[1:] if value.startswith("0") and len(value) > 1 else value
    )
    return normalized[target_columns].copy()


def split_report_warnings(report) -> tuple[list[str], list[str], list[str]]:
    """Separate blocking, medium-risk, and informational transformation warnings."""
    blocking: list[str] = []
    medium: list[str] = []
    info: list[str] = []

    if report.discarded > 0:
        medium.append(f"Se descartaron {report.discarded} filas durante la transformacion.")
    if report.discarded_accessories > 0:
        info.append(f"{report.discarded_accessories} filas fueron marcadas como accesorios.")
    if report.discarded_invalid_product > 0:
        medium.append(f"{report.discarded_invalid_product} filas no cumplian criterio de producto valido.")
    if report.leading_zero_real_loss_columns > 0:
        blocking.append("Hay perdida real de ceros iniciales en al menos una columna critica.")
    if report.invalid_yes_no_rows > 0:
        blocking.append(f"Hay {report.invalid_yes_no_rows} filas con valores invalidos SI/NO.")
    if report.unmatched_brand_names:
        medium.append(f"Marcas con incidencia abierta: {', '.join(report.unmatched_brand_names)}")
    category_review_rows = getattr(report, "category_review_rows", [])
    if category_review_rows:
        blocking.append(
            f"Hay {len(category_review_rows)} filas sin categoria: Coleccion vacia o no reconocida. "
            "Revisar antes de importar en Odoo."
        )
    for key, values in report.anomalous_values.items():
        blocking.append(f"Valores anómalos en {key}: {', '.join(values)}")
    return blocking, medium, info


def clean_ean(raw_value: object) -> str:
    value = str(raw_value).strip()
    if not value or value.lower() == "nan":
        return ""
    if value.lower().startswith("es."):
        value = value[3:]
    if value.endswith(".0"):
        value = value[:-2]
    return value.replace(" ", "")


def extract_luxoptica_products(dataframe: pd.DataFrame) -> list[dict[str, str]]:
    """Extract one request-manifest product per clean EAN."""
    if "Barcode" not in dataframe.columns:
        return []
    seen: set[str] = set()
    products: list[dict[str, str]] = []
    for _, row in dataframe.iterrows():
        ean = clean_ean(row["Barcode"])
        key = ean_key(ean) if ean_valid(ean) else ean
        if not ean or key in seen:
            continue
        seen.add(key)
        products.append(
            {
                "ean": ean,
                "modelo": str(row.get("Modelo", "")).strip(),
                "color": str(row.get("Color", "")).strip(),
            }
        )
    return products


def extract_clean_eans(dataframe: pd.DataFrame) -> list[str]:
    return [product["ean"] for product in extract_luxoptica_products(dataframe)]


def _ambiguous_luxoptica_keys(dataframe: pd.DataFrame) -> set[str]:
    if "Barcode" not in dataframe.columns:
        return set()
    identities: dict[str, dict[str, set[str]]] = {}
    for _, row in dataframe.iterrows():
        ean = clean_ean(row["Barcode"])
        if not ean_valid(ean):
            continue
        fields = identities.setdefault(ean_key(ean), {})
        for field in ("Marca", "Modelo", "Color", "Calibre"):
            value = row.get(field, "")
            if pd.notna(value) and str(value).strip():
                fields.setdefault(field, set()).add(str(value).strip().casefold())
    return {key for key, fields in identities.items() if any(len(values) > 1 for values in fields.values())}


def _live_luxottica_keys(request_root: Path) -> set[str]:
    request_root = Path(request_root)
    try:
        root_stat = request_root.stat()
    except FileNotFoundError:
        return set()
    except OSError as error:
        raise ValueError("No se puede consultar el almacén de solicitudes.") from error
    if not stat.S_ISDIR(root_stat.st_mode) or not os.access(request_root, os.R_OK | os.X_OK):
        raise ValueError("No se puede consultar el almacén de solicitudes.")
    path = request_root / "provider_requests.sqlite"
    try:
        path_stat = path.stat()
    except FileNotFoundError:
        return set()
    except OSError as error:
        raise ValueError("No se puede consultar el almacén de solicitudes.") from error
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("La ruta del almacén de solicitudes no es un archivo.")
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
            connection.execute("PRAGMA query_only=ON")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_eans'"
            ).fetchone()
            if not exists:
                raise ValueError("El almacén de solicitudes no contiene request_eans.")
            rows = connection.execute(
                "SELECT ean FROM request_eans WHERE provider='luxottica' AND live=1"
            ).fetchall()
    except sqlite3.Error as error:
        raise ValueError("No se pudo consultar el estado de solicitudes Luxottica.") from error
    return {ean_key(row[0]) for row in rows}


def _legacy_luxottica_keys(output_dir: Path) -> set[str]:
    output_dir = Path(output_dir)
    try:
        output_stat = output_dir.stat()
    except FileNotFoundError:
        return set()
    except OSError as error:
        raise ValueError("No se puede revisar el historial de lotes Luxottica.") from error
    if not stat.S_ISDIR(output_stat.st_mode) or not os.access(output_dir, os.R_OK | os.X_OK):
        raise ValueError("No se puede revisar el historial de lotes Luxottica.")
    keys = set()
    try:
        with os.scandir(output_dir) as entries:
            paths = []
            for entry in entries:
                if not entry.name.startswith("upc-products-images-request-") or not entry.name.endswith(".txt"):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError("El historial de lotes contiene una ruta no verificable.")
                paths.append(Path(entry.path))
    except OSError as error:
        raise ValueError("No se pudo revisar el historial de lotes Luxottica.") from error
    for path in paths:
        try:
            values = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as error:
            raise ValueError("No se pudo revisar un lote Luxottica anterior.") from error
        valid, _ = normalize_eans(values)
        keys.update(ean_key(ean) for ean in valid)
    return keys


def _unindexed_image_exists(root: Path, ean: str) -> bool:
    for form in code_variants(ean):
        folder = root / form
        try:
            folder_stat = folder.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("No se pudo verificar si hay imágenes sin registrar.") from error
        if stat.S_ISLNK(folder_stat.st_mode) or not stat.S_ISDIR(folder_stat.st_mode):
            return True
        pending = [folder]
        try:
            while pending:
                current = pending.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            return True
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in {
                            ".jpg", ".jpeg", ".png", ".webp",
                        }:
                            return True
        except OSError as error:
            raise ValueError("No se pudo verificar si hay imágenes sin registrar.") from error
    return False


def preview_luxoptica_request_products(
    dataframe: pd.DataFrame,
    *,
    request_root: Path | None = None,
    image_root: Path | None = None,
    catalog: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Read-only selection preview; only verified, truly absent products can be proposed."""
    products = extract_luxoptica_products(dataframe)
    root = Path(image_root) if image_root is not None else repository_root()
    request_root = Path(request_root) if request_root is not None else provider_data_root()
    output_dir = Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent / "docs" / "Luxoptica"
    valid, invalid = normalize_eans(product["ean"] for product in products)
    product_by_key = {ean_key(product["ean"]): product for product in products if ean_valid(product["ean"])}
    ambiguous = _ambiguous_luxoptica_keys(dataframe)

    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        report = {"catalog": {"status": "inaccesible", "blocker": "raiz_imagenes_no_disponible"},
                  "coverage": {}}
    else:
        report = catalog_coverage(
            valid, catalog=catalog, required_views=("frontal", "lateral", "perspectiva"),
            image_root=root, verify_files=True,
        )
    live = _live_luxottica_keys(request_root) | _legacy_luxottica_keys(output_dir)
    rows = []
    for ean in invalid:
        rows.append({"EAN": ean, "Modelo": "", "Color": "", "Estado": "EAN inválido",
                     "Motivo": "Código inválido o ambiguo"})
    for ean in valid:
        product = product_by_key[ean_key(ean)]
        coverage = report["coverage"].get(ean)
        if report["catalog"]["status"] != "consultado" or coverage is None:
            state, reason = "Bloqueado", "Catálogo o cobertura no disponibles"
        elif ean_key(ean) in live:
            state, reason = "Solicitud pendiente", "Ya existe una solicitud Luxottica vigente"
        elif ean_key(ean) in ambiguous:
            state, reason = "Revisión", "Identidad contradictoria para el mismo EAN"
        elif coverage["label"] == "completas":
            state, reason = "Con imágenes", "Vistas técnicas completas y archivos verificados"
        elif coverage["label"] == "parciales":
            state, reason = "Parcial", "No se solicita automáticamente una cobertura parcial"
        elif coverage["label"] == "sin_registros" and not _unindexed_image_exists(root, ean):
            state, reason = "Ausente", "Sin registros ni archivos de imagen en el repositorio"
        elif coverage["label"] == "sin_registros":
            state, reason = "Revisión", "Hay archivos de imagen no registrados en el catálogo"
        else:
            state, reason = "Revisión", "Hay registros sin vistas válidas o verificables"
        rows.append({"EAN": ean, "Modelo": product["modelo"], "Color": product["color"],
                     "Estado": state, "Motivo": reason})
    return {
        "rows": rows,
        "candidates": [row["EAN"] for row in rows if row["Estado"] == "Ausente"],
        "catalog": report["catalog"],
        "summary": {state: sum(row["Estado"] == state for row in rows)
                    for state in ("Ausente", "Con imágenes", "Parcial", "Solicitud pendiente",
                                  "Revisión", "Bloqueado", "EAN inválido")},
    }


def generate_luxoptica_request_files(
    dataframe: pd.DataFrame,
    output_dir: Path,
    batch_size: int = 250,
    max_total_eans: int | None = None,
    *,
    request_root: Path | None = None,
    image_root: Path | None = None,
    catalog: Path | None = None,
) -> list[Path]:
    """Persist only absent, verified EANs in the shared request store and export manual TXT lots."""
    if not 1 <= int(batch_size) <= 250:
        raise ValueError("El lote Luxottica debe contener entre 1 y 250 EAN.")
    request_root = Path(request_root) if request_root is not None else provider_data_root()
    preview = preview_luxoptica_request_products(
        dataframe, request_root=request_root, image_root=image_root, catalog=catalog, output_dir=output_dir,
    )
    if preview["catalog"]["status"] != "consultado":
        raise ValueError("No se pueden preparar solicitudes: el catálogo no está disponible.")
    eligible = set(preview["candidates"])
    products = [product for product in extract_luxoptica_products(dataframe) if product["ean"] in eligible]
    if max_total_eans is not None and max_total_eans > 0:
        products = products[:max_total_eans]
    if not products:
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = ProviderSettings("luxottica", max_eans_per_request=batch_size)
    store = ProviderStore(request_root)
    planned = store.plan(settings, [product["ean"] for product in products], mode="manual")
    if planned["invalid"]:
        raise ValueError("La lista cambió durante la preparación; revise los EAN inválidos.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    generated_files: list[Path] = []
    products_by_key = {ean_key(product["ean"]): product for product in products}
    for index, request_id in enumerate(planned["created"], start=1):
        batch = [products_by_key[ean_key(item["ean"])] for item in store.get(request_id)["eans"]]
        file_name = f"upc-products-images-request-{timestamp}-lote-{index:03d}.txt"
        file_path = output_dir / file_name
        file_path.write_text("\n".join(product["ean"] for product in batch) + "\n", encoding="utf-8")
        manifest_path = file_path.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps({"request_id": request_id, "products": batch}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        generated_files.append(file_path)
    return generated_files