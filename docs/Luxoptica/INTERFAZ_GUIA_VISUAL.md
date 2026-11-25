# Guía de interfaz: solicitudes Luxottica

> Esta guía describe el flujo local actual. Las capturas y ejemplos de botones anteriores no
> acreditan la disponibilidad ni el estado de sistemas productivos.

## Importador MASTERDATA

1. Carga el fichero origen y revisa las advertencias de transformación.
2. En **Solicitud de imágenes (Luxottica)**, revisa la vista previa por EAN.
3. Solo se pueden preparar EAN válidos sin imágenes ni solicitud activa. EAN completos,
   parciales, con archivos sin catalogar o con cobertura desconocida se excluyen o requieren
   revisión.
4. Si el catálogo, el repositorio de imágenes, el almacén compartido o el historial de lotes
   no están disponibles, la preparación se bloquea.
5. Confirma la selección para crear la solicitud en el almacén compartido y descargar el TXT
   y manifiesto. Cada lote contiene como máximo 250 EAN.

La app no envía lotes al portal. El envío es manual y requiere autorización específica; una
marca local de estado no acredita que el portal recibiera o procesara el lote.

## Pedidos Odoo

La consulta Odoo es explícita y de solo lectura. Las solicitudes preparadas desde pedidos usan
el mismo almacén y deduplicación UPC/EAN que MASTERDATA, conservan evidencia de línea/pedido
y solo admiten códigos sin registros de imagen. Los parciales o no verificables no se proponen.

La fecha común inicial es `2026-11-01`, configurable con auditoría. Cambiarla no reprocesa
pedidos; los pedidos piloto Kering explícitamente permitidos conservan su excepción acotada.

## Recepción e importación

El monitor Graph es independiente de las solicitudes. Su presencia en el repositorio o un
contenedor iniciado no demuestra actividad o éxito. Verifica runtime, recibos y errores según
la [guía de validación local Cloud](../CLOUD_LOCAL_VALIDATION.md). No ejecutes lectura de correo
ni descargas reales sin autorización específica.

Las imágenes canónicas se guardan por EAN en `IMAGE_REPOSITORY_ROOT/<EAN>`. La biblioteca y
los aliases de mercado no activan por sí mismos una publicación remota.

## Sesión recordada

La restauración de la sesión tras recrear la app está cubierta por pruebas offline. Requiere
`AUTH_COOKIE_SECRET` de al menos 32 caracteres y persistencia del almacén de revocaciones.
La persistencia real tras F5 en Cloud sigue pendiente de validación autorizada; si falta el
secreto, la opción **Recordarme** se deshabilita.
