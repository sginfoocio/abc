from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from db_config import load_db_config


@dataclass
class DryRunReport:
    total_rows: int
    creates: int
    updates: int
    conflicts: int
    unresolved_brands: int
    duplicate_barcodes_in_file: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "creates": self.creates,
            "updates": self.updates,
            "conflicts": self.conflicts,
            "unresolved_brands": self.unresolved_brands,
            "duplicate_barcodes_in_file": self.duplicate_barcodes_in_file,
        }


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def normalize_text(value: str) -> str:
    return " ".join(safe_text(value).lower().split())


def load_excel_as_text(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        df[col] = df[col].map(safe_text)
    return df


def load_odoo_snapshot() -> tuple[pd.DataFrame, dict[str, int]]:
    cfg = load_db_config()
    url = URL.create(
        "postgresql+psycopg2",
        username=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
    )
    engine = create_engine(url)

    barcode_query = text(
        """
        SELECT
            pp.barcode,
            pp.id AS product_product_id,
            pt.id AS product_template_id,
            pt.active,
            pt.name,
            dpb.name AS brand_name,
            pt.default_code,
            pt.modelo
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN diagonal_product_brand dpb ON dpb.id = pt.brand_id
        WHERE pp.barcode IS NOT NULL AND TRIM(pp.barcode) <> ''
        """
    )
    brand_query = text(
        """
        SELECT id, name
        FROM diagonal_product_brand
        WHERE name IS NOT NULL
        """
    )

    with engine.connect() as conn:
        barcode_df = pd.read_sql_query(barcode_query, conn)
        brand_df = pd.read_sql_query(brand_query, conn)

    barcode_df = barcode_df.fillna("")
    for col in barcode_df.columns:
        barcode_df[col] = barcode_df[col].map(safe_text)

    brand_map = {
        normalize_text(row["name"]): int(row["id"])
        for _, row in brand_df.fillna("").iterrows()
        if safe_text(row["name"])
    }
    return barcode_df, brand_map


def analyze_masterdata_against_odoo(masterdata_df: pd.DataFrame, odoo_barcodes_df: pd.DataFrame, brand_map: dict[str, int]) -> tuple[pd.DataFrame, DryRunReport]:
    df = masterdata_df.copy()
    df["UPC"] = df["UPC"].map(safe_text)
    df["Nombre de la marca"] = df["Nombre de la marca"].map(safe_text)
    df["Código del modelo"] = df["Código del modelo"].map(safe_text)
    df["__row_excel"] = df.index + 2

    existing_counts = odoo_barcodes_df["barcode"].value_counts().to_dict()
    duplicate_in_file = int(df["UPC"].duplicated(keep=False).sum())

    detail_rows: list[dict[str, Any]] = []
    creates = 0
    updates = 0
    conflicts = 0
    unresolved_brands = 0

    for _, row in df.iterrows():
        barcode = row["UPC"]
        brand_name = row["Nombre de la marca"]
        brand_id = brand_map.get(normalize_text(brand_name))
        brand_ok = brand_id is not None
        if not brand_ok:
            unresolved_brands += 1

        matches = odoo_barcodes_df[odoo_barcodes_df["barcode"] == barcode].copy()
        match_count = len(matches)
        if match_count == 0:
            action = "create"
            creates += 1
        elif match_count == 1:
            action = "update"
            updates += 1
        else:
            action = "conflict"
            conflicts += 1

        detail_rows.append(
            {
                "row_excel": int(row["__row_excel"]),
                "upc": barcode,
                "brand_name": brand_name,
                "brand_exists_in_odoo": "SI" if brand_ok else "NO",
                "brand_id": brand_id or "",
                "model_code": row["Código del modelo"],
                "odoo_barcode_matches": match_count,
                "duplicate_barcode_in_file": "SI" if existing_counts.get(barcode, 0) > 1 or bool(df[df["UPC"] == barcode].shape[0] > 1) else "NO",
                "dry_run_action": action,
            }
        )

    detail_df = pd.DataFrame(detail_rows)
    report = DryRunReport(
        total_rows=len(df),
        creates=creates,
        updates=updates,
        conflicts=conflicts,
        unresolved_brands=unresolved_brands,
        duplicate_barcodes_in_file=duplicate_in_file,
    )
    return detail_df, report


def build_summary_markdown(report: DryRunReport, input_path: Path, detail_path: Path | None) -> str:
    lines = [
        "# Dry Run Odoo Import",
        "",
        f"- Archivo analizado: {input_path}",
        f"- Registros evaluados: {report.total_rows}",
        f"- Altas potenciales: {report.creates}",
        f"- Actualizaciones potenciales: {report.updates}",
        f"- Conflictos por barcode en Odoo: {report.conflicts}",
        f"- Marcas no resueltas: {report.unresolved_brands}",
        f"- Duplicados de barcode en fichero: {report.duplicate_barcodes_in_file}",
        "",
        "## Criterio",
        "",
        "- `create`: el UPC no existe en `product_product.barcode`.",
        "- `update`: el UPC existe una sola vez en `product_product.barcode`.",
        "- `conflict`: el UPC existe varias veces en Odoo y requiere revision manual.",
    ]
    if detail_path:
        lines.extend(["", "## Artefactos", "", f"- Detalle CSV: {detail_path}"])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida un MASTERDATA transformado contra Odoo sin grabar nada.")
    parser.add_argument("--input", required=True, help="Ruta del Excel MASTERDATA transformado")
    parser.add_argument("--report", default="", help="Ruta opcional del reporte JSON")
    parser.add_argument("--detail", default="", help="Ruta opcional del CSV detalle")
    parser.add_argument("--summary", default="", help="Ruta opcional del resumen Markdown")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    report_path = Path(args.report) if args.report else None
    detail_path = Path(args.detail) if args.detail else None
    summary_path = Path(args.summary) if args.summary else None

    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")

    masterdata_df = load_excel_as_text(input_path)
    odoo_barcodes_df, brand_map = load_odoo_snapshot()
    detail_df, report = analyze_masterdata_against_odoo(masterdata_df, odoo_barcodes_df, brand_map)

    print("=" * 80)
    print("DRY RUN ODOO IMPORT")
    print("=" * 80)
    print(f"Registros evaluados: {report.total_rows}")
    print(f"Altas potenciales: {report.creates}")
    print(f"Actualizaciones potenciales: {report.updates}")
    print(f"Conflictos por barcode en Odoo: {report.conflicts}")
    print(f"Marcas no resueltas: {report.unresolved_brands}")
    print(f"Duplicados de barcode en fichero: {report.duplicate_barcodes_in_file}")

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte JSON: {report_path}")

    if detail_path:
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_df.to_csv(detail_path, index=False, encoding="utf-8-sig")
        print(f"Detalle CSV: {detail_path}")

    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(build_summary_markdown(report, input_path, detail_path), encoding="utf-8")
        print(f"Resumen MD: {summary_path}")


if __name__ == "__main__":
    main()