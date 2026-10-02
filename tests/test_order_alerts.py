from concurrent.futures import ThreadPoolExecutor

import json

import pytest

from order_alerts import (
    OrderAlertStore,
    build_alert_email_html,
    import_legacy_notification_file,
)


def test_concurrent_claims_only_reserve_order_once(tmp_path) -> None:
    database_path = tmp_path / "alerts.sqlite3"
    stores = [OrderAlertStore(database_path), OrderAlertStore(database_path)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        batches = list(
            executor.map(
                lambda index: stores[index % 2].claim_orders([123], now=100),
                range(8),
            )
        )

    assert sum(len(batch.order_ids) for batch in batches) == 1


def test_failed_and_expired_claims_can_be_retried(tmp_path) -> None:
    store = OrderAlertStore(tmp_path / "alerts.sqlite3")

    failed_batch = store.claim_orders([123], now=100, lease_seconds=10)
    store.mark_failed(failed_batch.batch_id, "mail service unavailable", now=101)
    retry_batch = store.claim_orders([123], now=102, lease_seconds=10)
    assert retry_batch.order_ids == (123,)

    expired_lease_batch = store.claim_orders([456], now=100, lease_seconds=10)
    takeover_batch = store.claim_orders([456], now=111, lease_seconds=10)
    assert expired_lease_batch.order_ids == (456,)
    assert takeover_batch.order_ids == (456,)


def test_sent_orders_are_suppressed_and_legacy_ids_are_imported(tmp_path) -> None:
    store = OrderAlertStore(tmp_path / "alerts.sqlite3")
    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text(json.dumps([10]), encoding="utf-8")
    import_legacy_notification_file(store, legacy_file)
    batch = store.claim_orders([10, 20], now=101)
    store.mark_sent(batch.batch_id, "alerts@example.test", now=102)

    next_batch = store.claim_orders([10, 20], now=103)

    assert batch.order_ids == (20,)
    assert next_batch.order_ids == ()


def test_invalid_legacy_file_fails_closed(tmp_path) -> None:
    store = OrderAlertStore(tmp_path / "alerts.sqlite3")
    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text("invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="registro legacy"):
        import_legacy_notification_file(store, legacy_file)


def test_alert_email_html_escapes_order_fields() -> None:
    html = build_alert_email_html(
        [
            {
                "pedido": "<script>",
                "cliente": "A & B",
                "direccion_entrega": "",
                "date_order": "2026-10-02",
                "state": "sale",
                "invoice_status": "to invoice",
                "amount_total": 10,
            }
        ]
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html
