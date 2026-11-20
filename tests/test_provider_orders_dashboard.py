"""Read-only dashboard regressions. No Odoo, NAS or portal credentials."""
from datetime import date
from types import SimpleNamespace

import provider_orders_dashboard as dashboard


def line(order_id, ean):
    return SimpleNamespace(
        order_id=order_id, order=f"PO-{order_id}", date="2026-11-20",
        supplier="Proveedor", ean=ean, reference="SKU", product="Gafas",
    )


def test_aggregate_preserves_orders_and_repeated_lines():
    lines = [line(1, "889652646725"), line(1, "889652646725"), line(2, "889652646725")]
    coverage = {"889652646725": {
        "label": "parciales", "basis": "solo_metadatos_del_catalogo",
        "views": ["frontal", "lateral"], "missing_views": ["perspectiva"],
        "records": 4,
    }}
    orders, rows = dashboard.aggregate(lines, coverage)
    assert len(orders) == 2
    assert len(orders[1]["lines"]) == 2
    assert rows[0]["Referencias"] == 2
    assert rows[0]["Procesamiento"] == "No consultado"
    assert rows[0]["Cobertura"] == "Parcial / pendiente"


def test_unknown_catalog_is_not_missing_or_processed():
    orders, rows = dashboard.aggregate([line(5, "889652646725")], {})
    assert rows[0]["Cobertura"] == "Desconocida"
    assert rows[0]["Procesamiento"] == "No consultado"
    assert orders[5]["lines"][0]["coverage"]["label"] == "desconocida"


def test_query_uses_confirmed_association_and_read_only_catalog(monkeypatch, tmp_path):
    calls = []
    def partners(root, provider):
        calls.append(("partners", provider))
        return (566,)
    def read(engine, ids, first, last, *, max_lines):
        calls.append(("orders", tuple(ids), max_lines))
        return [line(15, "889652646725")], False
    def catalog(codes):
        calls.append(("catalog", list(codes)))
        return {"catalog": {"status": "consultado"}, "coverage": {
            "889652646725": {"label": "completas", "basis": "solo_metadatos_del_catalogo",
                             "views": ["frontal", "lateral", "perspectiva"],
                             "missing_views": [], "records": 3}}}
    monkeypatch.setattr(dashboard, "confirmed_partner_ids", partners)
    monkeypatch.setattr(dashboard, "read_order_lines", read)
    monkeypatch.setattr(dashboard, "catalog_coverage", catalog)
    result = dashboard.query_orders(object(), tmp_path, "kering", date(2026, 11, 1),
                                    date(2026, 11, 20), limit=15)
    assert calls == [("partners", "kering"), ("orders", (566,), 15),
                     ("catalog", ["889652646725"])]
    assert result["rows"][0]["Cobertura"] == "Completa (catálogo)"
    assert result["rows"][0]["Procesamiento"] == "No consultado"


def test_no_hidden_odoo_read_when_dashboard_is_rendered(monkeypatch):
    class Stop(Exception):
        pass
    fake = SimpleNamespace(
        session_state={"auth_role": "admin"},
        subheader=lambda *a, **k: None,
        caption=lambda *a, **k: None,
        selectbox=lambda label, opts, **k: opts[0],
        columns=lambda n: [SimpleNamespace(__enter__=lambda s:s, __exit__=lambda s,*a:None)
                            for _ in range(n)],
    )
    # Explicit queries are separate from the pure aggregation tests.
    assert callable(dashboard.render)
    assert "read_order_lines" not in dashboard.render.__code__.co_names
