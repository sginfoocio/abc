import unittest

import pandas as pd

from transform_luxottica_masterdata import transform_masterdata


class TransformMasterdataTests(unittest.TestCase):
    def _base_row(self) -> dict[str, str]:
        return {
            "Punto de venta": "0001",
            "Código del modelo": "0RB-1234/56",
            "Calibre": "52",
            "Color": "000123",
            "UPC": "1234567890123",
            "Nombre de la marca": "RAY BAN",
            "Código de marca": "RB",
            "Colección": "General",
            "Género": "Unisex",
            "Forma": "Rectangular",
            "Tipo": "Aro Completo",
            "Nombre del modelo": "WAYFARER",
            "Descripción del color": "BLACK",
            "Material del frente": "Metal",
            "Color del frontal": "BLACK",
            "Material de las lentes": "Cristal",
            "Color de las lentes": "GREEN",
            "Fotocromático": "",
            "Polarizado": "X",
            "Largo de varilla": "145",
            "Dimensión del puente": "18",
            "PVP sugerido": "100",
            "PVO": "50",
            "Categoría": "",
        }

    def test_rayban_meta_detected(self) -> None:
        row = self._base_row()
        row["Colección"] = "Meta Smart"
        row["Nombre del modelo"] = "Wayfarer Meta"

        output, report, _, _, _ = transform_masterdata(pd.DataFrame([row]), color_map={})

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Nombre de la marca"], "Ray Ban Meta")

    def test_brand_name_is_canonicalized_from_brand_map(self) -> None:
        row = self._base_row()
        row["Nombre de la marca"] = "EMPORIO ARMANI"
        row["Código de marca"] = "EA"

        output, report, _, _, _ = transform_masterdata(
            pd.DataFrame([row]),
            color_map={},
            brand_map={"emporio armani": "Emporio Armani"},
        )

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Nombre de la marca"], "Emporio Armani")

    def test_brand_alias_tiffany_is_resolved(self) -> None:
        row = self._base_row()
        row["Nombre de la marca"] = "TIFFANY"
        row["Código de marca"] = "TF"

        output, report, _, _, _ = transform_masterdata(
            pd.DataFrame([row]),
            color_map={},
            brand_map={"tiffany & co.": "Tiffany & Co."},
        )

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Nombre de la marca"], "Tiffany & Co.")
        self.assertEqual(report.unmatched_brand_names, [])

    def test_brand_code_is_derived_from_model_code_when_missing(self) -> None:
        row = self._base_row()
        row["Nombre de la marca"] = "DOLCE & GABBANA"
        row["Código de marca"] = ""
        row["Código del modelo"] = "0DG-1322"

        output, report, _, _, _ = transform_masterdata(
            pd.DataFrame([row]),
            color_map={},
            brand_map={"dolce gabbana": "Dolce Gabbana"},
        )

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Código de marca"], "DG")

    def test_generics_remains_as_incidence(self) -> None:
        row = self._base_row()
        row["Nombre de la marca"] = "GENERICS"
        row["Código de marca"] = "GN"

        output, report, _, _, brand_audit = transform_masterdata(
            pd.DataFrame([row]),
            color_map={},
            brand_map={"ray ban": "Ray Ban"},
        )

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Nombre de la marca"], "GENERICS")
        self.assertEqual(report.unmatched_brand_names, ["GENERICS"])
        self.assertEqual(brand_audit.iloc[0]["marca_origen"], "GENERICS")
        self.assertEqual(brand_audit.iloc[0]["existe_en_bd"], "NO")

    def test_invalid_product_discarded(self) -> None:
        valid = self._base_row()
        invalid = self._base_row()
        invalid["UPC"] = "9988776655443"
        invalid["Código del modelo"] = ""

        output, report, discarded_audit, _, _ = transform_masterdata(pd.DataFrame([valid, invalid]), color_map={})

        self.assertEqual(report.discarded_invalid_product, 1)
        self.assertEqual(report.total_output, 1)
        self.assertEqual(len(output), 1)
        self.assertIn("producto_invalido", discarded_audit["motivo_descarte"].tolist()[0])

    def test_accessory_by_model_code_prefix_is_discarded(self) -> None:
        valid = self._base_row()
        accessory = self._base_row()
        accessory["UPC"] = "1111111111111"
        accessory["Código del modelo"] = "AOO9188LS"
        accessory["Colección"] = ""
        accessory["Nombre del modelo"] = "HOLBROOK"
        accessory["Categoría"] = ""
        accessory["Tipo"] = ""

        output, report, discarded_audit, _, _ = transform_masterdata(pd.DataFrame([valid, accessory]), color_map={})

        self.assertEqual(report.discarded_accessories, 1)
        self.assertEqual(len(output), 1)
        reasons = ";".join(discarded_audit["motivo_descarte"].tolist())
        self.assertIn("accesorio", reasons)

    def test_accessory_whitelist_keeps_model_code(self) -> None:
        valid = self._base_row()
        accessory = self._base_row()
        accessory["UPC"] = "2222222222222"
        accessory["Código del modelo"] = "AOO9188LS"
        accessory["Nombre del modelo"] = "HOLBROOK"
        accessory["Colección"] = ""
        accessory["Categoría"] = ""
        accessory["Tipo"] = ""

        output, report, discarded_audit, _, _ = transform_masterdata(
            pd.DataFrame([valid, accessory]),
            color_map={},
            accessory_whitelist={"AOO9188LS"},
        )

        self.assertEqual(report.discarded_accessories, 0)
        self.assertEqual(report.accessory_whitelist_kept, 1)
        self.assertEqual(len(output), 2)
        self.assertEqual(len(discarded_audit), 0)


if __name__ == "__main__":
    unittest.main()
