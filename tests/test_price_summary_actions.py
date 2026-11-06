"""Summary counts, denominators and navigation use stored data only."""
import json

import pandas as pd
import pytest
import requests
from streamlit.testing.v1 import AppTest

import price_analytics as analytics
from tests.test_price_analytics import _fresh, _offers, _own


def sample():
    return pd.DataFrame([
        {"ean": str(i), "brand": brand, "model": "M1", "commercial_name": "Modelo 1", "color": "001",
         "own_price_eur": own, "difference_eur": gap, "difference_vs_competitor_pct": gap,
         "competitor_min_eur": 100 if gap is not None else None, "best_competitor": "Tienda",
         "last_checked_at": "2026-11-06T08:00:00+00:00" if gap is not None else None}
        for i, (brand, own, gap) in enumerate([
            ("Alpha", 90, -10), ("Beta", 100, 0), ("Beta", 120, 20),
            ("Alpha", None, None), ("Beta", 100, None),
        ])
    ])


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Summary must never perform HTTP")
    monkeypatch.setattr(requests.Session, "send", deny)


def test_action_cards_partition_catalogue_without_equating_missing_own_with_missing_rival():
    summary = sample()
    groups = {key: analytics.summary_segment(summary, key)["ean"].tolist()
              for key in analytics.SUMMARY_SEGMENTS}
    assert groups == {"cheaper": ["0"], "equal": ["1"], "higher": ["2"],
                      "missing_own": ["3"], "missing_competitor": ["4"]}
    assert len({ean for group in groups.values() for ean in group}) == len(summary)
    filtered = summary[summary["brand"] == "Beta"]
    assert analytics.summary_segment(filtered, "cheaper").empty
    assert analytics.summary_segment(filtered, "higher")["ean"].tolist() == ["2"]


def test_gap_distribution_preserves_boundaries_ties_and_valid_sample():
    gaps = [-16, -15, -5, -0.01, 0, 0.01, 5, 15, 16, None]
    summary = pd.DataFrame({"difference_eur": gaps, "difference_vs_competitor_pct": gaps})
    bins = analytics.price_gap_distribution(summary)
    assert bins["count"].tolist() == [1, 1, 2, 1, 2, 1, 1]
    assert bins["count"].sum() == 9
    assert bins["share_pct"].sum() == pytest.approx(100)
    figure = analytics.price_gap_figure(summary)
    assert list(figure.data[0].y) == bins["count"].tolist()
    assert "%{y} productos" in figure.data[0].hovertemplate


def test_brand_ranking_counts_products_and_keeps_missing_comparison_out_of_percentages():
    ranking = analytics.summary_ranking(sample(), pd.DataFrame()).set_index("name")
    assert ranking.loc["Alpha", ["catalog_products", "compared", "cheaper", "equal", "higher"]].tolist() == [2, 1, 1, 0, 0]
    assert ranking.loc["Beta", ["catalog_products", "compared", "cheaper", "equal", "higher"]].tolist() == [3, 2, 0, 1, 1]
    assert ranking.loc["Alpha", "competitive_pct"] == 100
    assert ranking.loc["Beta", "competitive_pct"] == 50
    assert ranking.loc["Beta", "mean_gap_pct"] == 10
    missing = sample().iloc[[3, 4]]
    empty_comparisons = analytics.summary_ranking(missing, pd.DataFrame())
    assert empty_comparisons["competitive_pct"].isna().all()
    assert empty_comparisons["mean_gap_pct"].isna().all()


def test_competitor_ranking_uses_each_rival_price_and_latest_valid_offer(tmp_path, monkeypatch):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(1, 100)])
    _offers(store, [("Tienda EUR", 80, 2), ("Tienda EUR", 90, 1), ("Tienda USD", 110, 1)])
    summary, _ = store.dashboard(7)
    offers = store.current_offers(7)
    ranking = analytics.summary_ranking(summary, offers, by="competitor").set_index("name")
    assert ranking.loc["Tienda EUR", "compared"] == 1
    assert ranking.loc["Tienda EUR", "higher"] == 1
    assert ranking.loc["Tienda EUR", "mean_gap_pct"] == pytest.approx(100 / 90 * 100 - 100)
    assert ranking.loc["Tienda USD", "cheaper"] == 1
    selected_offers = offers[offers["competitor_name"] == "Tienda USD"]
    scoped = analytics.dashboard_scope(summary, offers, competitors=["Tienda USD"])
    selected = analytics.summary_ranking(scoped, selected_offers, by="competitor")
    assert selected["name"].tolist() == ["Tienda USD"]
    assert selected.iloc[0]["competitive_pct"] == 100


def test_empty_summary_never_fabricates_competitiveness():
    empty = sample().iloc[:0]
    assert all(analytics.summary_segment(empty, key).empty for key in analytics.SUMMARY_SEGMENTS)
    assert analytics.summary_ranking(empty, pd.DataFrame()).empty
    assert analytics.summary_ranking(empty, pd.DataFrame(), by="competitor").empty
    assert analytics.price_gap_distribution(empty)["count"].sum() == 0
    with pytest.raises(ValueError):
        analytics.summary_segment(empty, "unknown")


def test_cards_show_matching_products_and_clear_without_touching_shared_filters():
    records = sample().astype(object).where(pd.notna(sample()), None).to_dict("records")
    app = AppTest.from_string(
        "import json, pandas as pd, streamlit as st\n"
        "from app_pages.price_comparator import _render_summary_cards\n"
        "st.session_state.setdefault('dashboard_brand', ['Alpha'])\n"
        f"_render_summary_cards(pd.DataFrame(json.loads({json.dumps(records)!r})))\n"
    ).run(timeout=30)
    assert not app.exception
    metrics = [(metric.label, metric.value) for metric in app.metric]
    assert metrics == [
        ("Productos · Más barato", "1"),
        ("Productos · Mismo precio", "1"),
        ("Productos · Más caro", "1"),
        ("Productos · Sin precio Vytria", "1"),
        ("Productos · Sin oferta comparable", "1"),
    ]
    app.button(key="summary_view_missing_own").click().run(timeout=30)
    assert not app.exception
    assert app.dataframe[0].value["EAN"].tolist() == ["3"]
    assert app.session_state["dashboard_brand"] == ["Alpha"]
    app.button(key="summary_view_higher").click().run(timeout=30)
    assert app.dataframe[0].value["EAN"].tolist() == ["2"]
    app.button(key="summary_clear_focus").click().run(timeout=30)
    assert not app.exception and not app.dataframe


@pytest.mark.parametrize("rival_price, higher_pct, competitive_pct, counts", [
    (None, "Sin datos", "Sin datos", (0, 0, 0, 0, 1)),
    (90, "100,0 %", "0,0 %", (0, 0, 1, 0, 0)),
    (100, "0,0 %", "100,0 %", (0, 1, 0, 0, 0)),
    (110, "0,0 %", "100,0 %", (1, 0, 0, 0, 0)),
])
def test_summary_percentages_and_product_counts_have_distinct_labels(
    tmp_path, monkeypatch, rival_price, higher_pct, competitive_pct, counts,
):
    store = _fresh(tmp_path, monkeypatch)
    _own(store, [(5, 100.0)])
    if rival_price is not None:
        _offers(store, [("Tienda EUR", rival_price, 1)])
    monkeypatch.setenv("PRICE_COMPARATOR_DB_PATH", str(store.database_path))

    app = AppTest.from_string(
        "from app_pages.price_comparator import render_price_comparator_page\n"
        "render_price_comparator_page()\n"
    ).run(timeout=60)

    assert not app.exception
    labels = [metric.label for metric in app.metric]
    card_labels = (
        "Productos · Más barato", "Productos · Mismo precio", "Productos · Más caro",
        "Productos · Sin precio Vytria", "Productos · Sin oferta comparable",
    )
    for label in (*card_labels, "Productos comparados", "Igual o más barato",
                  "Más caro", "Diferencia media vs. mejor"):
        assert labels.count(label) == 1
    values = {metric.label: metric.value for metric in app.metric}
    assert values["Más caro"] == higher_pct
    assert values["Igual o más barato"] == competitive_pct
    assert values["Productos comparados"] == ("0 / 1" if rival_price is None else "1 / 1")
    assert [values[label] for label in card_labels] == [str(count) for count in counts]


def test_ranking_toggle_does_not_present_a_brand_total_as_competitor_observations():
    records = sample().astype(object).where(pd.notna(sample()), None).to_dict("records")
    app = AppTest.from_string(
        "import json, pandas as pd\n"
        "from app_pages.price_comparator import _render_summary_ranking\n"
        f"_render_summary_ranking(pd.DataFrame(json.loads({json.dumps(records)!r})), pd.DataFrame())\n"
    ).run(timeout=30)
    assert not app.exception and "Marca" in app.dataframe[0].value
    app.radio(key="summary_ranking_mode").set_value("Por competidor").run(timeout=30)
    assert not app.exception and not app.dataframe
    assert any("No hay comparaciones válidas" in item.value for item in app.info)
