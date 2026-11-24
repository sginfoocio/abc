from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
import psycopg2

BACKUP_DIR = Path(__file__).resolve().parent / "database"
TABLES_TO_BACKUP = [
    "product_product",
    "product_template",
    "stock_move",
    "stock_quant",
    "stock_location",
    "sale_order_line",
]

DB_CONFIG = {
    "host": "10.3.0.11",
    "port": 5432,
    "dbname": "UAT",
    "user": "user_sg_informatica",
    "password": "ActKn5JnFxx3Xc",
}


def ensure_backup_dir() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(**DB_CONFIG)


def _format_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return str(value).replace(".", ",")
    if isinstance(value, Decimal):
        return format(value, 'f').replace(".", ",")
    return str(value)


def dump_table_to_csv(table_name: str, connection: psycopg2.extensions.connection) -> Path:
    target_file = BACKUP_DIR / f"{table_name}.csv"
    with connection.cursor() as cursor, target_file.open("w", newline="", encoding="utf-8") as csvfile:
        cursor.execute(f"SELECT * FROM {table_name}")
        headers = [col.name for col in cursor.description]
        writer = csv.writer(csvfile, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        for row in cursor:
            writer.writerow([_format_value(value) for value in row])
    return target_file


def main() -> None:
    ensure_backup_dir()

    print(f"Backup directory: {BACKUP_DIR}")
    with connect() as connection:
        for table in TABLES_TO_BACKUP:
            print(f"Exportando tabla: {table}")
            csv_path = dump_table_to_csv(table, connection)
            print(f"  -> {csv_path}")

    print("Odoo es de solo lectura; no se generan comandos de restauración.")


if __name__ == "__main__":
    main()
