# Envío de solicitudes Luxottica

> Para el servicio de envío desde pedidos y sus verificaciones de puesta en marcha, consulta
> [Envío programado desde pedidos de compra](./AUTOMATIZACION_SOLICITUD_IMAGENES.md#envío-programado-desde-pedidos-de-compra).

## Envío asistido desde Masterdata

1. En MASTERDATA, revisa la vista previa de solicitudes y confirma únicamente EAN válidos,
   ausentes de imágenes y sin una solicitud activa.
2. Descarga el lote TXT (máximo 250 EAN) y revisa su manifiesto.
3. Una persona autorizada envía el archivo manualmente por el portal, usando el procedimiento
   aprobado y conservando evidencia fuera de Git.
4. Registra en la app solo el estado manual que corresponda. Esa marca no acredita por sí sola
   que el portal recibiera o procesara el lote.
5. La recepción y descarga de respuestas se valida por separado; consulta la
   [guía del flujo actual](./AUTOMATIZACION_SOLICITUD_IMAGENES.md) y la
   [política de validación local Cloud](../CLOUD_LOCAL_VALIDATION.md).

Desde Masterdata el envío sigue siendo manual. Si el resultado de un envío remoto es ambiguo, no lo reenvíes:
solicita reconciliación con evidencia antes de preparar otra solicitud.
