from datetime import datetime, timezone

import pandas as pd
import pytest

import price_analytics as pa
from price_comparator import iso_datetimes, vytria_price_states
from tests.test_price_analytics import EAN, _fresh, _offers, _own
from vytria_price_sync import VytriaPriceSync


NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def _history(store, own_dates, offer_dates, check=None):
    with store._connect() as con, con:
        own_ids = [r[0] for r in con.execute("SELECT id FROM vytria_price_history ORDER BY id")]
        offer_ids = [r[0] for r in con.execute("SELECT id FROM offers ORDER BY id")]
        for row_id, at in zip(own_ids, own_dates):
            con.execute("UPDATE vytria_price_history SET recorded_at=? WHERE id=?", (at, row_id))
        for row_id, at in zip(offer_ids, offer_dates):
            con.execute("UPDATE offers SET captured_at=? WHERE id=?", (at, row_id))
        if check is not None:
            con.execute("""INSERT OR REPLACE INTO offer_checks(ean,competitor_id,market,last_offer_id,last_ok_at,checks)
                VALUES(?,?,?,?,?,1)""", (EAN, "eur-example", "ES", offer_ids[-1], check))


def test_mixed_iso_precision_and_offsets_use_actual_instants_without_writes(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(3, 100), (2, 80)])
    _offers(store, [("Tienda EUR", 90, 3), ("Tienda EUR", 85, 2)])
    _history(store, ["2026-11-03T08:00:00+00:00", "2026-11-04T09:23:42.222950+00:00"],
             ["2026-11-03T08:00:00Z", "2026-11-04T11:23:42+02:00"],
             "2026-11-04T10:00:00.123456Z")
    with store._connect() as con:
        before = list(con.iterdump())

    series = pa.competitiveness_series(store, 2, NOW, expiry_days=1)

    assert series["best_price_pct"].tolist() == [0, 100]
    assert series["compared"].tolist() == [1, 1]
    assert len(pa.competitiveness_figure(series).data) > 0
    with store._connect() as con:
        assert list(con.iterdump()) == before


def test_chronological_cutoff_excludes_future_offset_and_selects_latest_instant(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(3, 100), (2, 80), (1, 200)])
    _offers(store, [("Tienda EUR", 90, 2)])
    _history(store, ["2026-11-04T10:00:00+02:00", "2026-11-04T07:30:00-03:00",
                     "2026-11-04T11:00:00-03:00"], ["2026-11-04T08:00:00Z"])

    series = pa.competitiveness_series(store, 0, NOW)

    assert series["best_price_pct"].tolist() == [100]
    assert series["compared"].tolist() == [1]


def test_multiple_products_with_mixed_precision_and_checks(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(2, 80)])
    _offers(store, [("Tienda EUR", 90, 2)])
    own = pa._vytria_frame(store)
    offers = pa._offer_frame(store)
    own = pd.concat([own, own.assign(ean="other", price=100)], ignore_index=True)
    offers = pd.concat([offers, offers.assign(ean="other")], ignore_index=True)
    own["recorded_at"] = ["2026-11-04T09:23:42+00:00", "2026-11-04T09:23:42.222950+00:00"]
    offers["captured_at"] = ["2026-11-04T09:23:42.222950+00:00", "2026-11-04T09:23:42+00:00"]
    offers["last_ok_at"] = ["2026-11-04T10:00:00Z", "2026-11-04T10:00:00.123456Z"]
    monkeypatch.setattr(pa, "_vytria_frame", lambda *args: own.copy())
    monkeypatch.setattr(pa, "_offer_frame", lambda *args: offers.copy())
    series = pa.competitiveness_series(store, 0, NOW)
    assert series["compared"].tolist() == [2]
    assert series["best_price_pct"].tolist() == [50]


@pytest.mark.parametrize("invalid", [None, "", "not-a-date"])
def test_invalid_observations_are_excluded_and_invalid_check_cannot_refresh(tmp_path, monkeypatch, invalid, caplog):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(3, 80), (2, 200)])
    _offers(store, [("Tienda EUR", 90, 3), ("Tienda EUR", 70, 2)])
    # SQLite date columns are NOT NULL; pandas can still receive nulls from joins/legacy readers.
    own = pa._vytria_frame(store)
    offers = pa._offer_frame(store)
    own["recorded_at"] = ["2026-11-03T10:00:00", invalid]
    offers["captured_at"] = ["2026-11-03T10:00:00Z", invalid]
    offers["last_ok_at"] = [invalid, "2026-11-04T10:00:00Z"]
    monkeypatch.setattr(pa, "_vytria_frame", lambda *args: own.copy())
    monkeypatch.setattr(pa, "_offer_frame", lambda *args: offers.copy())

    series = pa.competitiveness_series(store, 0, NOW, expiry_days=2)
    assert series["best_price_pct"].tolist() == [100]
    assert series["compared"].tolist() == [1]
    assert pa.competitiveness_series(store, 0, NOW, expiry_days=1).empty
    if invalid == "not-a-date":
        assert "recorded_at" in caplog.text and "captured_at" in caplog.text


def test_checks_never_leak_backwards_or_shorten_freshness(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(2, 80)])
    _offers(store, [("Tienda EUR", 90, 2)])
    _history(store, ["2026-11-03T08:00:00Z"], ["2026-11-03T08:00:00Z"],
             "2026-11-04T11:00:00-03:00")
    assert pa.competitiveness_series(store, 0, NOW, expiry_days=1).empty
    _history(store, ["2026-11-04T10:00:00Z"], ["2026-11-04T10:00:00Z"],
             "2026-11-02T10:00:00Z")
    assert pa.competitiveness_series(store, 0, NOW.replace(tzinfo=None), expiry_days=1)["compared"].tolist() == [1]


def test_price_series_and_changes_sort_instants_and_preserve_window_anchor(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(pa, "_utc", lambda: NOW)
    _own(store, [(3, 100), (2, 80)])
    _offers(store, [("Tienda EUR", 90, 3), ("Tienda EUR", 70, 2)])
    _history(store, ["2026-11-02T10:00:00+02:00", "2026-11-02T07:30:00-03:00"],
             ["2026-11-02T10:00:00+02:00", "2026-11-02T07:30:00-03:00"],
             "2026-11-04T11:00:00+02:00")
    series = pa.price_series(store, EAN, 1, now=NOW.replace(tzinfo=None))
    assert series[series["series"] == "Vytria"]["eur"].tolist() == [80]
    assert series[series["series"] == "Tienda EUR"]["eur"].tolist() == [70, 70]
    changes = pa.recent_changes(store, days=3)
    assert set(changes["after"]) == {80, 70}
    figure = pa.price_figure(series)
    assert figure.data


@pytest.mark.parametrize("value", [None, "", "invalid", float("nan"), pd.NaT])
def test_missing_or_invalid_price_state_is_expired(value):
    assert pa.price_state(value, 1, NOW) == pa.STATE_EXPIRED


def test_naive_price_state_is_utc():
    assert pa.price_state("2026-11-04T11:00:00", 1, NOW.replace(tzinfo=None)) == pa.STATE_CURRENT


def test_vytria_validity_uses_iso_dates_and_invalid_dates_are_not_current(caplog):
    frame = pd.DataFrame({
        "ean": ["a", "b", "c", "d", "e"],
        "synced_at": ["2026-11-03T12:00:00Z", "2026-11-04T09:23:42.222950+00:00",
                      "2026-11-04T12:30:00+02:00", None, "invalid"],
        "stale_since": [None] * 5, "validation_status": ["validated"] * 5, "comparable": [1] * 5,
    })
    result = vytria_price_states(frame, 7, set(frame.ean), now=NOW)
    assert result["usable"].tolist() == [False, True, True, False, False]
    assert result.loc[2, "valid_until"] == pd.Timestamp("2026-11-05T10:30:00Z")
    assert "synced_at" in caplog.text


def test_sync_history_read_uses_chronological_order_without_writes(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(3, 100), (2, 80)])
    _history(store, ["2026-11-02T10:00:00+02:00", "2026-11-02T07:30:00-03:00"], [])
    with store._connect() as con:
        before = list(con.iterdump())
    sync = VytriaPriceSync(store)
    assert sync.price_history(EAN)["price"].tolist() == [80, 100]
    sync.last_run()
    sync.prices()
    with store._connect() as con:
        assert list(con.iterdump()) == before


def test_iso_parser_handles_sqlite_pandas_and_python_dates():
    values = pd.Series(["2026-11-04T09:23:42.222950+00:00", datetime(2026, 11, 4, 9),
                        pd.Timestamp("2026-11-04T11:00:00+02:00"), None, pd.NaT, float("nan"), ""])
    parsed = iso_datetimes(values, "test_date")
    assert str(parsed.dtype).endswith(", UTC]")
    assert parsed.iloc[1] == parsed.iloc[2] == pd.Timestamp("2026-11-04T09:00:00Z")
    assert parsed.iloc[3:].isna().all()


def test_current_dashboard_and_kpis_use_same_instants_as_history(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(3, 100), (2, 80)])
    _offers(store, [("Tienda EUR", 90, 2)])
    _history(store, ["2025-03-01T10:00:00+02:00", "2025-03-01T07:30:00-03:00"],
             ["2025-03-01T12:30:00+02:00"])
    with store._connect() as con, con:
        con.execute("UPDATE vytria_prices SET price=80,synced_at='2025-03-01T07:30:00-03:00'")
        con.execute("""INSERT INTO own_prices(ean,market,price,currency,fx_to_eur,captured_at,source,fingerprint)
            VALUES(?,'ES',70,'EUR',1,'2025-03-01T07:30:00-03:00','manual','date-test')""", (EAN,))
    # 12:30+02:00 is before the fixed clock at 12:00Z, despite its text order.
    # It is also within a one-hour window, despite 12:30+02:00 > 12:00Z lexically.
    summary, _ = store.dashboard(1)
    assert pa.kpis(summary, 0)["best_price_pct"] == 100
    with store._connect() as con, con:
        con.execute("UPDATE offers SET captured_at='2025-03-01T09:30:00-03:00'")
    # 09:30-03:00 is in the future, despite its text being earlier than noon.
    summary, _ = store.dashboard(1)
    assert pa.kpis(summary, 0)["comparable"] == 0
    assert store.current_offers(1).empty


def test_current_comparison_orders_manual_prices_and_competitor_updates_in_utc(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _offers(store, [("Tienda EUR", 90, 2), ("Tienda EUR", 95, 1)])
    _history(store, [], ["2025-03-01T10:00:00+02:00", "2025-03-01T07:30:00-03:00"])
    with store._connect() as con, con:
        for at, price in [("2025-03-01T10:00:00+02:00", 100), ("2025-03-01T07:30:00-03:00", 80)]:
            con.execute("""INSERT INTO own_prices(ean,market,price,currency,fx_to_eur,captured_at,source,fingerprint)
                VALUES(?,'ES',?,'EUR',1,?,'manual',?)""", (EAN, price, at, at))
    summary, _ = store.dashboard(1)
    assert summary.iloc[0]["own_price_eur"] == 80
    assert summary.iloc[0]["competitor_min_eur"] == 95
    assert pa.kpis(summary, 0)["best_price_pct"] == 100


@pytest.mark.parametrize("check", ["invalid", "2025-03-01T14:00:00Z", "2025-02-25T10:00:00Z"])
def test_current_and_historical_freshness_ignore_invalid_or_misordered_checks(tmp_path, monkeypatch, check):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(0.1, 80)])
    _offers(store, [("Tienda EUR", 90, 0.1)])
    _history(store, ["2025-03-01T10:00:00Z"], ["2025-03-01T10:00:00Z"], check)
    with store._connect() as con:
        before = list(con.iterdump())
    summary, _ = store.dashboard(1)
    assert pa.kpis(summary, 0)["best_price_pct"] == 100
    assert len(store.current_offers(1)) == 1
    series = pa.competitiveness_series(store, 0, datetime(2025, 3, 1, 12, tzinfo=timezone.utc), expiry_days=1)
    assert series["best_price_pct"].tolist() == [100]
    with store._connect() as con:
        assert list(con.iterdump()) == before
