import unittest

import pandas as pd

from validate_masterdata_odoo_dryrun import analyze_masterdata_against_odoo


class ValidateMasterdataOdooDryRunTests(unittest.TestCase):
    def test_classifies_create_update_and_conflict(self) -> None:
        masterdata = pd.DataFrame(
            [
                {"UPC": "100", "Nombre de la marca": "Ray Ban", "Código del modelo": "RB1"},
                {"UPC": "200", "Nombre de la marca": "Oakley", "Código del modelo": "OO1"},
                {"UPC": "300", "Nombre de la marca": "GENERICS", "Código del modelo": "GN1"},
            ]
        )
        odoo = pd.DataFrame(
            [
                {"barcode": "200", "product_product_id": "10", "product_template_id": "20"},
                {"barcode": "300", "product_product_id": "11", "product_template_id": "21"},
                {"barcode": "300", "product_product_id": "12", "product_template_id": "22"},
            ]
        )
        brand_map = {"ray ban": 1, "oakley": 2}

        detail, report = analyze_masterdata_against_odoo(masterdata, odoo, brand_map)

        self.assertEqual(report.creates, 1)
        self.assertEqual(report.updates, 1)
        self.assertEqual(report.conflicts, 1)
        self.assertEqual(report.unresolved_brands, 1)
        self.assertEqual(detail["dry_run_action"].tolist(), ["create", "update", "conflict"])

    def test_detects_duplicate_barcodes_in_file(self) -> None:
        masterdata = pd.DataFrame(
            [
                {"UPC": "100", "Nombre de la marca": "Ray Ban", "Código del modelo": "RB1"},
                {"UPC": "100", "Nombre de la marca": "Ray Ban", "Código del modelo": "RB2"},
            ]
        )
        odoo = pd.DataFrame([], columns=["barcode", "product_product_id", "product_template_id"])
        brand_map = {"ray ban": 1}

        detail, report = analyze_masterdata_against_odoo(masterdata, odoo, brand_map)

        self.assertEqual(report.duplicate_barcodes_in_file, 2)
        self.assertEqual(set(detail["duplicate_barcode_in_file"].tolist()), {"SI"})


if __name__ == "__main__":
    unittest.main()