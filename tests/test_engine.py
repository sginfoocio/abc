import pandas as pd

from engine import run_abcd_engine


def test_days_without_sales_for_d_override_changes_classification() -> None:
    reference_date = pd.Timestamp("2025-01-01")
    products = pd.DataFrame(
        [
            {
                "PVO": 100,
                "Stock": 4,
                "Primera Compra": reference_date - pd.Timedelta(days=365),
                "Última Venta": reference_date - pd.Timedelta(days=200),
                "Num_Ventas_180D": 0,
            }
        ]
    )

    default_result = run_abcd_engine(products, reference_date=reference_date)
    custom_result = run_abcd_engine(
        products,
        days_without_sales_for_d=250,
        reference_date=reference_date,
    )

    assert default_result.loc[0, "ABCD"] == "D"
    assert custom_result.loc[0, "ABCD"] == "A"