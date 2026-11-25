from datetime import date
import sqlite3

import pytest

from provider_order_policy import (
    DEFAULT_ORDER_START_DATE,
    OrderPolicyUnavailable,
    get_order_start_date,
    set_order_start_date,
)


def test_common_order_start_defaults_to_november_2026(tmp_path):
    assert get_order_start_date(tmp_path) == DEFAULT_ORDER_START_DATE == date(2026, 11, 1)


def test_common_order_start_changes_are_persisted_and_audited_once(tmp_path):
    updated = set_order_start_date(tmp_path, date(2026, 11, 3), actor="admin@example.test")
    assert updated == {
        "old_value": "2026-11-01", "new_value": "2026-11-03", "changed": True,
    }
    assert get_order_start_date(tmp_path) == date(2026, 11, 3)
    unchanged = set_order_start_date(tmp_path, date(2026, 11, 3), actor="admin@example.test")
    assert unchanged["changed"] is False
    with sqlite3.connect(tmp_path / "provider_order_policy.sqlite") as connection:
        rows = connection.execute(
            "SELECT actor,old_value,new_value FROM order_start_audit"
        ).fetchall()
    assert rows == [("admin@example.test", "2026-11-01", "2026-11-03")]


@pytest.mark.parametrize("value, actor", [(None, "admin"), (date(2026, 11, 1), "")])
def test_order_policy_rejects_invalid_changes(tmp_path, value, actor):
    with pytest.raises(ValueError):
        set_order_start_date(tmp_path, value, actor=actor)


def test_corrupt_order_policy_fails_closed(tmp_path):
    path = tmp_path / "provider_order_policy.sqlite"
    path.write_bytes(b"not sqlite")
    with pytest.raises(OrderPolicyUnavailable):
        get_order_start_date(tmp_path)


def test_invalid_or_unreadable_order_policy_location_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "provider_order_policy.sqlite"
    path.mkdir()
    with pytest.raises(OrderPolicyUnavailable):
        get_order_start_date(tmp_path)

    path.rmdir()
    monkeypatch.setattr("provider_order_policy.os.access", lambda *_args: False)
    with pytest.raises(OrderPolicyUnavailable):
        get_order_start_date(tmp_path)
