# Validación local de Cloud

Esta guía aplica la [política obligatoria del repositorio](../AGENTS.md#política-obligatoria-de-validación-local-previa-a-integración-y-despliegue).

## Preparación

1. Identifica rama, SHA, versión de Python, dependencias y configuración local. No reutilices estados persistentes como destino de escritura de la prueba.
2. Comprueba permisos efectivos, montajes, DNS, VPN, certificados, límites y acceso autorizado. Usa los mecanismos existentes para credenciales sin imprimir contraseñas, tokens ni cookies. Para catálogos y estados productivos, usa acceso de solo lectura; no reconstruyas índices ni generes miniaturas persistentes.
3. Define la muestra, la ventana temporal y las operaciones permitidas antes de conectar. Para cualquier efecto externo, registra la autorización específica, los registros afectados y los límites.

## Panel de proveedores

| Recorrido | Evidencia mínima |
|---|---|
| P0.1 · Odoo y NAS | De uno a tres pedidos por proveedor con asociación comercial confirmada. Contrastar fechas, estado, pedido, líneas, cantidades, variante, EAN/UPC original, modelo, color y calibre. Verificar límites, filtros y paginación sin consultas masivas. Comprobar identidad, existencia, SHA-256, vistas técnicas, extras, cobertura e historial sin modificar catálogo ni originales. |
| P0.2 · Procesamiento | Validar primero preparación, selección, confirmación, locks, duplicados, reintentos, historial y resultados parciales sin efectos externos. Registrar por separado cualquier ejecución real, autorizada por proveedor, pedido, referencias y efectos. |
| P0.3 · Biblioteca y ZIP | Abrir imágenes reales, volver al pedido conservando filtros y comprobar identidad, vistas, integridad y exclusiones para Original, Farfetch y Miinto. No escribir en NAS durante la vista previa. Si se autoriza generar ZIP, usar almacenamiento temporal local aislado y verificar que no contiene originales antes de limpiarlo. |

Para Kering, no ampliar el piloto existente sin autorización. Para Luxottica, no consumir mensajes ni atribuir recepciones globales a pedidos sin evidencia. Para Marcolin, no reenviar solicitudes pendientes. Para Sáfilo y NuORDER, verificar el circuito asistido y los permisos sin asumir automatización ni API. Prueba importaciones únicamente en almacenamiento local aislado hasta obtener autorización para producir efectos.

## Registro y decisión

Por prueba, registra: fecha, entorno, SHA, proveedor, identificador de muestra no sensible, sistema real, operación, resultado esperado y obtenido, evidencia técnica, errores, correcciones y estado (**correcto**, **incorrecto** o **bloqueado**, con causa). Separa los resultados en cuatro grupos: offline con mocks, local con servicios reales, procesamiento real autorizado y verificación posterior al despliegue. No copies credenciales, archivos productivos ni datos identificativos al repositorio.

Para un defecto demostrado, reproduce, corrige en su rama, añade una regresión y vuelve a comprobar la operación real y las pruebas offline afectadas con el nuevo HEAD y su CI. Si no puedes repetir la prueba real, mantenla bloqueada.

Antes de fusionar, confirma el CI obligatorio, las aprobaciones, ausencia de conflictos y de defectos críticos, evidencia local del SHA y el estado del despliegue automático. Antes de desplegar, confirma además el SHA integrado, el digest verificado, los respaldos, el rollback, las automatizaciones y la autorización expresa. Sigue el [procedimiento de despliegue Cloud](../deployment/cloud/README.md).
