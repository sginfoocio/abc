# Dry Run Odoo Import

- Archivo analizado: docs\MasterData\MASTERDATA_prueba_grande_transformado.xlsx
- Registros evaluados: 12782
- Altas potenciales: 9060
- Actualizaciones potenciales: 3722
- Conflictos por barcode en Odoo: 0
- Marcas no resueltas: 0
- Duplicados de barcode en fichero: 0

## Criterio

- `create`: el UPC no existe en `product_product.barcode`.
- `update`: el UPC existe una sola vez en `product_product.barcode`.
- `conflict`: el UPC existe varias veces en Odoo y requiere revision manual.

## Artefactos

- Detalle CSV: docs\MasterData\MASTERDATA_prueba_grande_dryrun_odoo.csv
