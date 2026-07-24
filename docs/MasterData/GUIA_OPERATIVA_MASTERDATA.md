# Guia Operativa MASTERDATA Luxottica -> Odoo

## Objetivo

Ejecutar de forma repetible la transformacion de un Excel Luxottica a un fichero MASTERDATA listo para revision final e importacion en Odoo.

Tambien puede hacerse desde la web ABC en el apartado `Preimportación Odoo`, sin necesidad de lanzar comandos manuales.

## Entradas

- Excel origen Luxottica.
- Conexion a BD para diccionario de colores y marcas.
- Archivo opcional de excepciones de accesorios:
  - `docs/MasterData/accessory_whitelist_codes.txt`

## Salidas

- Excel MASTERDATA transformado.
- Reporte JSON de validacion.
- Auditoria de descartes.
- Auditoria de ceros iniciales.
- Auditoria de marcas.
- Resumen ejecutivo en Markdown.

## Comando recomendado

```powershell
& "c:/Users/Ruben/OneDrive - Diagonal Eyewear/Proyectos/ABC/.venv/Scripts/python.exe" \
  "transform_luxottica_masterdata.py" \
  --input "docs/MasterData/Prueba grande.xlsx" \
  --output "docs/MasterData/MASTERDATA_prueba_grande_transformado.xlsx" \
  --report "docs/MasterData/MASTERDATA_prueba_grande_validacion.json" \
  --export-audits \
  --accessory-whitelist-file "docs/MasterData/accessory_whitelist_codes.txt" \
  --executive-summary "docs/MasterData/MASTERDATA_prueba_grande_resumen_ejecutivo.md"
```

## Flujo web

1. Abrir la app ABC.
2. Entrar en `Preimportación Odoo`.
3. En la pestaña `Transformación`, subir el Excel origen Luxottica.
4. Revisar advertencias del lote.
5. Descargar el Excel MASTERDATA transformado y sus artefactos.
6. Si hace falta, usar la pestaña `Dry-run Odoo` para validar altas y actualizaciones sin grabar nada.

## Criterios de lectura del resultado

- `Perdida real de ceros iniciales (columnas)` debe ser `0`.
- `Filas invalidas SI/NO` debe ser `0`.
- `Marcas no resueltas contra BD` debe revisarse contra la auditoria de marcas.
- `Registros descartados` debe justificarse con la auditoria de descartes.

## Politicas actuales

- `GENERICS` se mantiene como incidencia y no se remapea automaticamente.
- `Ralph Lauren` se descarta; `Polo Ralph Lauren` se mantiene.
- Se permite whitelist de accesorios por codigo de modelo.

## Reproceso de un nuevo lote

1. Copiar el Excel origen en `docs/MasterData/`.
2. Ajustar nombres de salida para no sobreescribir lotes previos si hace falta historico.
3. Ejecutar el comando recomendado.
4. Revisar JSON y resumen ejecutivo.
5. Revisar auditorias si hay incidencias o descartes inesperados.
6. Validar negocio antes de importar en Odoo.

## Dry Run contra Odoo

Para validar si el lote parece alta, actualizacion o conflicto sin grabar nada:

```powershell
& "c:/Users/Ruben/OneDrive - Diagonal Eyewear/Proyectos/ABC/.venv/Scripts/python.exe" \
  "validate_masterdata_odoo_dryrun.py" \
  --input "docs/MasterData/MASTERDATA_prueba_grande_transformado.xlsx" \
  --report "docs/MasterData/MASTERDATA_prueba_grande_dryrun_odoo.json" \
  --detail "docs/MasterData/MASTERDATA_prueba_grande_dryrun_odoo.csv" \
  --summary "docs/MasterData/MASTERDATA_prueba_grande_dryrun_odoo.md"
```

Interpretacion:

- `create`: UPC no existe en Odoo.
- `update`: UPC existe una sola vez en Odoo.
- `conflict`: UPC aparece varias veces en Odoo y requiere revision manual.
