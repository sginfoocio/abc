from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any, Iterable


ORDER_ALERT_STATE_PATH_ENV = "ORDER_ALERT_STATE_PATH"
ORDER_ALERT_LEASE_SECONDS = 600


@dataclass(frozen=True)
class OrderAlertBatch:
    batch_id: str
    order_ids: tuple[int, ...]


def default_order_alert_store_path() -> Path:
    default_path = Path(__file__).resolve().parent / "masterdata_data" / "order_alerts.sqlite3"
    return Path(os.getenv(ORDER_ALERT_STATE_PATH_ENV, str(default_path)))


def build_alert_email_html(rows: Iterable[dict[str, Any]]) -> str:
    table_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(str(row.get(column, '') or ''))}</td>"
            for column in (
                "pedido",
                "cliente",
                "direccion_entrega",
                "date_order",
                "state",
                "invoice_status",
                "amount_total",
            )
        )
        + "</tr>"
        for row in rows
    )
    return (
        "<p>Se han detectado los siguientes pedidos NUEVOS de clientes vigilados en Odoo:</p>"
        "<table border='1' cellpadding='4' cellspacing='0'>"
        "<tr><th>Pedido</th><th>Cliente</th><th>Dirección entrega</th><th>Fecha</th><th>Estado</th>"
        "<th>Estado factura</th><th>Total</th></tr>"
        f"{table_rows}"
        "</table>"
    )


def import_legacy_notification_file(store: "OrderAlertStore", legacy_path: Path | str) -> None:
    source = Path(legacy_path)
    if not source.exists():
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo migrar el registro legacy de alertas: {source}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"El registro legacy de alertas tiene un formato inválido: {source}")
    store.import_legacy_sent(payload)


class OrderAlertStore:
    """Shared durable claims for manual and scheduled order-alert email sends."""

    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(database_path or default_order_alert_store_path())
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS order_notifications (
                    order_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL,
                    batch_id TEXT,
                    claimed_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    recipient TEXT,
                    updated_at INTEGER NOT NULL,
                    sent_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS order_notifications_batch_idx
                    ON order_notifications (batch_id, status);
                """
            )

    def import_legacy_sent(self, order_ids: Iterable[int], now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        values = [(int(order_id), timestamp) for order_id in order_ids]
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """
                INSERT OR IGNORE INTO order_notifications (
                    order_id, status, updated_at, sent_at
                ) VALUES (?, 'sent', ?, ?)
                """,
                ((order_id, timestamp, timestamp) for order_id, timestamp in values),
            )

    def claim_orders(
        self,
        order_ids: Iterable[int],
        now: int | None = None,
        lease_seconds: int = ORDER_ALERT_LEASE_SECONDS,
    ) -> OrderAlertBatch:
        timestamp = int(time.time()) if now is None else int(now)
        batch_id = uuid.uuid4().hex
        claimed: list[int] = []
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for order_id in dict.fromkeys(int(value) for value in order_ids):
                row = connection.execute(
                    "SELECT status, claimed_until, attempts FROM order_notifications WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                if row and row[0] == "sent":
                    continue
                if row and row[0] == "pending" and int(row[1] or 0) > timestamp:
                    continue

                attempts = int(row[2]) + 1 if row else 1
                connection.execute(
                    """
                    INSERT INTO order_notifications (
                        order_id, status, batch_id, claimed_until, attempts,
                        last_error, updated_at, sent_at
                    ) VALUES (?, 'pending', ?, ?, ?, NULL, ?, NULL)
                    ON CONFLICT (order_id) DO UPDATE SET
                        status = 'pending',
                        batch_id = excluded.batch_id,
                        claimed_until = excluded.claimed_until,
                        attempts = excluded.attempts,
                        last_error = NULL,
                        updated_at = excluded.updated_at,
                        sent_at = NULL
                    """,
                    (order_id, batch_id, timestamp + lease_seconds, attempts, timestamp),
                )
                claimed.append(order_id)
        return OrderAlertBatch(batch_id, tuple(claimed))

    def mark_sent(self, batch_id: str, recipient: str, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE order_notifications
                SET status = 'sent', recipient = ?, sent_at = ?, updated_at = ?,
                    claimed_until = NULL, last_error = NULL
                WHERE batch_id = ? AND status = 'pending'
                """,
                (recipient, timestamp, timestamp, batch_id),
            )

    def mark_failed(self, batch_id: str, error: str, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE order_notifications
                SET status = 'failed', last_error = ?, updated_at = ?, claimed_until = NULL
                WHERE batch_id = ? AND status = 'pending'
                """,
                (str(error)[:2000], timestamp, batch_id),
            )