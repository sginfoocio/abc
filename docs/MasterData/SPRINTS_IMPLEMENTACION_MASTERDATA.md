# Sprints de Implementacion: Luxottica -> MASTERDATA

## Sprint 0 - Cierre funcional (0.5-1 dia)
Objetivo: cerrar decisiones pendientes para evitar ambiguedades en ejecucion.

Tareas:
- Definir criterio exacto de producto valido para importacion.
- Definir regla exacta para identificar Ray Ban Meta.
- Confirmar defaults de Fotocromatico y Polarizado (propuesto: NO).
- Confirmar fuente de Punto de venta, PVP sugerido, PVO y Tipo cuando falten.

Entregable:
- Especificacion funcional v1.0 aprobada.

Criterio DoD:
- No quedan reglas abiertas.

## Sprint 1 - Ingesta y mapeo base (1 dia)
Objetivo: construir pipeline de lectura y mapeo a estructura final.

Tareas:
- Leer Excel en formato texto para preservar ceros iniciales.
- Estandarizar nombres de columnas y columnas obligatorias.
- Implementar mapeo de coleccion desde la columna H.

Entregable:
- Dataset base con 24 columnas de salida.

Criterio DoD:
- 100% de filas origen leidas sin errores tecnicos.

## Sprint 2 - Normalizacion de negocio (2 dias)
Objetivo: aplicar reglas maestras de depuracion y estandarizacion.

Tareas:
- Filtrar accesorios y marca Ralph Lauren (mantener Polo Ralph Lauren).
- Normalizar marcas (Dolce Gabbana, Ray Ban, Ray Ban Junior, Ray Ban Meta, Oakley).
- Normalizar modelo (quitar / y -, preservar texto y ceros).
- Integrar diccionarios de color desde BD:
  - diagonal_product_color
  - diagonal_product_color_dictionary
- Normalizar forma y materiales.
- Forzar genero y categoria a catalogos permitidos.
- Aplicar reglas de lentes, fotocromatico y polarizado.

Entregable:
- MASTERDATA candidato listo para validacion.

Criterio DoD:
- Campos normalizados solo con valores permitidos o incidencia registrada.

## Sprint 3 - Validaciones y control (1 dia)
Objetivo: asegurar integridad antes de exportar.

Tareas:
- Validar conteos (procesados, exportados, descartados).
- Validar integridad de UPC y deduplicacion por UPC.
- Validar conservacion de ceros iniciales.
- Validar SI/NO en Fotocromatico y Polarizado.
- Generar reporte de incidencias detectadas.

Entregable:
- Reporte de validacion JSON + resumen ejecutivo.

Criterio DoD:
- Incidencias criticas en 0 o justificadas con accion.

## Sprint 4 - Exportacion y handover (0.5 dia)
Objetivo: cerrar entrega lista para importacion Odoo.

Tareas:
- Exportar Excel final MASTERDATA.
- Documentar comando de ejecucion y checklist.
- Transferir procedimiento para lotes futuros.
- Ejecutar preimportacion segura contra Odoo sin grabar datos.

Entregable:
- Excel final + reporte + guia operativa.
- Dry-run Odoo con clasificacion create/update/conflict.

Criterio DoD:
- Archivo importable en Odoo y proceso repetible.

## Comando de ejecucion (MVP actual)

```powershell
& "c:/Users/Ruben/OneDrive - Diagonal Eyewear/Proyectos/ABC/.venv/Scripts/python.exe" \
  "transform_luxottica_masterdata.py" \
  --input "docs/MasterData/Archivo de producto_20260312122257 - ARCHIVO ORIGINAL ENVIADO POR LUXOTTICA.xlsx" \
  --output "docs/MasterData/MASTERDATA_transformado.xlsx" \
  --report "docs/MasterData/MASTERDATA_validacion.json" \
  --export-audits \
  --executive-summary "docs/MasterData/MASTERDATA_resumen_ejecutivo.md"
```

## Estado implementado (2026-07-24)

Completado en codigo:
- Fuente canonica de marcas desde BD: `diagonal_product_brand.name`.
- Regla explicita para Ray Ban Meta (deteccion en marca/coleccion/modelo/tipo).
- Criterio formal de producto valido para importacion: UPC + marca + codigo de modelo obligatorios.
- Nuevo conteo de descartes por producto invalido en reporte.
- Marcas sin match en BD se conservan como incidencia; decision actual: `GENERICS` no se descarta ni se remapea.
- Exportacion automatica de auditorias CSV con `--export-audits`:
  - descartes por fila y motivo.
  - resumen de ceros iniciales (legacy vs perdida real post-filtros).
  - auditoria de marcas resueltas/no resueltas.
- Exportacion automatica de resumen ejecutivo con `--executive-summary`.
- Whitelist opcional de accesorios para excepciones negocio:
  - `--accessory-whitelist-codes "COD1,COD2"`
  - `--accessory-whitelist-file "docs/MasterData/accessory_whitelist_codes.txt"`
- Script de dry-run contra Odoo sin escritura: `validate_masterdata_odoo_dryrun.py`.
- Apartado web nuevo en la app ABC para:
  - subir Excel origen Luxottica.
  - transformar dentro de la web.
  - mostrar advertencias.
  - descargar Excel transformado, JSON, resumen y auditorias.
  - ejecutar dry-run Odoo opcional sobre el resultado.

Resultado en `Prueba grande.xlsx`:
- Procesados: 14176
- Exportados: 12780 (sin whitelist)
- Descartados: 1396 (sin whitelist)
- Producto invalido: 0
- Perdida real de ceros iniciales: 0 columnas

Resultado con whitelist (`AOO0005LS`):
- Exportados: 12782
- Descartados: 1394
- Excepciones whitelist conservadas: 2
- Marcas no resueltas contra BD: 1 (`GENERICS`)

Resultado dry-run Odoo sobre `MASTERDATA_prueba_grande_transformado.xlsx`:
- Registros evaluados: 12782
- Altas potenciales: 9060
- Actualizaciones potenciales: 3722
- Conflictos por barcode en Odoo: 0
- Marcas no resueltas en el lote transformado: 0
- Duplicados de barcode en fichero: 0
