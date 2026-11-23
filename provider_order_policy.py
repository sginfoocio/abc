from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat


DEFAULT_ORDER_START_DATE = date(2026, 11, 1)
POLICY_DATABASE = "provider_order_policy.sqlite"


class OrderPolicyUnavailable(RuntimeError):
    pass


def provider_data_root(env=None) -> Path:
    environment = os.environ if env is None else env
    explicit = environment.get("PROVIDER_DATA_ROOT")
    if explicit:
        return Path(explicit)
    kering = environment.get("KERING_DATA_ROOT")
    if kering:
        return Path(kering).parent / "providers"
    return Path(__file__).resolve().parent / "masterdata_data" / "providers"


def order_policy_path(root: Path) -> Path:
    return Path(root) / POLICY_DATABASE


def get_order_start_date(root: Path) -> date:
    root = Path(root)
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return DEFAULT_ORDER_START_DATE
    except OSError as error:
        raise OrderPolicyUnavailable("No se puede consultar la configuración común de pedidos.") from error
    if not stat.S_ISDIR(root_stat.st_mode) or not os.access(root, os.R_OK | os.X_OK):
        raise OrderPolicyUnavailable("No se puede consultar la configuración común de pedidos.")
    path = order_policy_path(root)
    try:
        policy_stat = path.stat()
    except FileNotFoundError:
        return DEFAULT_ORDER_START_DATE
    except OSError as error:
        raise OrderPolicyUnavailable("No se pudo leer la configuración común de pedidos.") from error
    if not stat.S_ISREG(policy_stat.st_mode):
        raise OrderPolicyUnavailable("La ruta de configuración común no es un archivo.")
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT value FROM settings WHERE key='order_start_date'"
            ).fetchone()
    except sqlite3.Error as error:
        raise OrderPolicyUnavailable("No se pudo leer la configuración común de pedidos.") from error
    if row is None:
        return DEFAULT_ORDER_START_DATE
    try:
        return date.fromisoformat(row[0])
    except (TypeError, ValueError) as error:
        raise OrderPolicyUnavailable("La fecha común de pedidos no es válida.") from error


def set_order_start_date(root: Path, value: date, *, actor: str) -> dict:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("fecha_de_inicio_invalida")
    if not str(actor).strip():
        raise ValueError("administrador_obligatorio")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = order_policy_path(root)
    changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(path, timeout=10, isolation_level=None) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS order_start_audit ("
                "id INTEGER PRIMARY KEY, changed_at TEXT NOT NULL, actor TEXT NOT NULL, "
                "old_value TEXT NOT NULL, new_value TEXT NOT NULL)"
            )
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT value FROM settings WHERE key='order_start_date'"
            ).fetchone()
            old_value = previous[0] if previous else DEFAULT_ORDER_START_DATE.isoformat()
            new_value = value.isoformat()
            if old_value != new_value:
                connection.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES('order_start_date',?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (new_value, changed_at),
                )
                connection.execute(
                    "INSERT INTO order_start_audit(changed_at,actor,old_value,new_value) VALUES(?,?,?,?)",
                    (changed_at, str(actor).strip(), old_value, new_value),
                )
            connection.execute("COMMIT")
    except sqlite3.Error as error:
        raise OrderPolicyUnavailable("No se pudo guardar la configuración común de pedidos.") from error
    return {"old_value": old_value, "new_value": new_value, "changed": old_value != new_value}
