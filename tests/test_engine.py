import pandas as pd

from engine import run_abcd_engine


def test_eligible_product_without_recent_sales_is_classified_d() -> None:
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

    result = run_abcd_engine(products, reference_date=reference_date)

    assert result.loc[0, "ABCD"] == "D"