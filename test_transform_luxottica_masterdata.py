import unittest

import pandas as pd

from transform_luxottica_masterdata import (
    find_dictionary_values_not_resolved,
    normalize_color,
    normalize_shape,
    transform_masterdata,
)


class TransformMasterdataTests(unittest.TestCase):
    def test_color_dictionary_is_checked_before_alphanumeric_fallback(self) -> None:
        color_map = {"BLACK": "Negro", "GREEN": "Verde", "901": "Negro"}

        self.assertEqual(normalize_color("BLACK", color_map), "Negro")
        self.assertEqual(normalize_color("GREEN", color_map), "Verde")
        self.assertEqual(normalize_color("901", color_map), "Negro")

    def test_inferred_category_is_normalized_to_monturas(self) -> None:
        row = self._base_row()
        row["Color de las lentes"] = ""
        row["Tipo"] = "Aro completo"

        output, report, _, _, _ = transform_masterdata(pd.DataFrame([row]), color_map={})

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Categoría"], "MONTURAS")

    def test_color_code_is_preserved_in_masterdata(self) -> None:
        row = self._base_row()
        row["Color"] = "900642"
        output, report, *_ = transform_masterdata(
            pd.DataFrame([row]),
            color_map={"900642": "No debe aplicarse"},
        )

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Color"], "900642")

    def test_shape_dictionary_resolves_odoo_names(self) -> None:
        shape_map = {
            "aviator": "Aviator",
            "cat eye": "Cat Eye",
            "rectangular/cuadrada": "Rectangular/Cuadrada",
        }

        self.assertEqual(normalize_shape("Aviator", shape_map), "Aviator")
        self.assertEqual(normalize_shape("Cat Eye", shape_map), "Cat Eye")
        self.assertEqual(normalize_shape("Rectangular/Cuadrada", shape_map), "Rectangular/Cuadrada")

    def test_unresolved_dictionary_values_are_reported(self) -> None:
        source = pd.DataFrame(
            {
                "Color": ["BLACK", "CUSTOM_GREEN", "UNKNOWN_COLOR"],
                "Forma": ["Aviator", "CUSTOM_SHAPE", "UNKNOWN_SHAPE"],
            }
        )
        unresolved = find_dictionary_values_not_resolved(
            source,
            color_map={"BLACK": "Negro"},
            shape_map={"aviator": "Aviator"},
            dictionary_rules=[
                {"Columna": "Color", "Valor": "CUSTOM_GREEN", "Transformado": "Verde"},
                {"Columna": "Forma", "Valor": "CUSTOM_SHAPE", "Transformado": "Geométrica"},
            ],
        )

        self.assertEqual(
            {(item["Columna"], item["Valor"]) for item in unresolved},
            {("Forma", "UNKNOWN_SHAPE")},
        )

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
        self.assertEqual(output.iloc[0]["Marca"], "Ray Ban Meta")

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
        self.assertEqual(output.iloc[0]["Marca"], "Emporio Armani")

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
        self.assertEqual(output.iloc[0]["Marca"], "Tiffany & Co.")
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
        self.assertEqual(output.iloc[0]["Modelo"], "DG1322")

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
        self.assertEqual(output.iloc[0]["Marca"], "GENERICS")
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

    def test_pvp_from_alias_column_is_preserved(self) -> None:
        row = self._base_row()
        row.pop("PVP sugerido", None)
        row["PVP"] = "173"

        output, report, _, _, _ = transform_masterdata(pd.DataFrame([row]), color_map={})

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["PVP"], "173")

    def test_collection_comes_from_model_name_column(self) -> None:
        row = self._base_row()
        row["Colección"] = "VALOR_ORIGINAL_COLECCION"
        row["Nombre del modelo"] = "WAYFARER PUFFER"

        output, report, _, _, _ = transform_masterdata(pd.DataFrame([row]), color_map={})

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Colección"], "WAYFARER PUFFER")

    def test_collection_comes_from_column_l_even_if_header_changes(self) -> None:
        row = self._base_row()
        row["Nombre del modelo"] = "VALOR_COLUMNA_L"
        df = pd.DataFrame([row]).rename(columns={"Nombre del modelo": "L_ORIGEN"})

        output, report, _, _, _ = transform_masterdata(df, color_map={})

        self.assertEqual(report.total_output, 1)
        self.assertEqual(output.iloc[0]["Colección"], "VALOR_COLUMNA_L")


if __name__ == "__main__":
    unittest.main()
