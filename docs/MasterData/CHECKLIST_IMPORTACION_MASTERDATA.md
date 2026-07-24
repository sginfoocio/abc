# Checklist de Importacion MASTERDATA

## Antes de ejecutar

- La `.venv` esta activa o se usa el interprete correcto.
- El archivo Excel origen existe y no esta bloqueado por Excel/OneDrive.
- La BD es accesible para leer colores y marcas.
- El archivo `accessory_whitelist_codes.txt` esta revisado si aplica.

## Despues de ejecutar

- Se genero el Excel MASTERDATA.
- Se genero el JSON de validacion.
- Se genero el resumen ejecutivo `.md`.
- Se generaron las auditorias CSV.

## Validaciones minimas

- `Perdida real de ceros iniciales (columnas) = 0`.
- `Filas invalidas SI/NO = 0`.
- `Marcas no resueltas contra BD` revisadas.
- `Registros descartados` revisados con auditoria.
- Dry-run Odoo revisado para altas, actualizaciones y conflictos por barcode.

## Validaciones de negocio

- Los descartes por accesorios son esperados.
- Las excepciones whitelist conservadas son esperadas.
- `GENERICS` queda aceptada como incidencia abierta si aparece.
- No hay marcas inesperadas fuera de `diagonal_product_brand`.

## Antes de importar en Odoo

- El area de negocio valida el resumen ejecutivo.
- El area de negocio valida incidencias abiertas.
- Se conserva evidencia del lote: Excel, JSON, CSV y resumen ejecutivo.
