# Microsoft Graph y recepción Luxottica

> **Estado no verificado.** Este documento no autoriza registrar aplicaciones, conceder o ampliar
> permisos, leer correo, descargar adjuntos ni cambiar servicios. El estado actual de Cloud,
> OAuth, buzón, monitor y persistencia debe comprobarse con autorización específica siguiendo la
> [validación local de Cloud](../CLOUD_LOCAL_VALIDATION.md).

## Alcance

Graph se usa para la recepción/descarga y es independiente de la preparación de solicitudes
MASTERDATA. La app prepara TXT/manifiestos manuales; no inicia sesión ni envía archivos al portal.
La existencia de código, configuración histórica o un contenedor no acredita funcionamiento.

## Antes de una validación real

1. Define por escrito el entorno, buzón, intervalo de mensajes, muestra, permisos, escrituras
   locales y límites.
2. Empieza por lecturas de solo lectura y usa credenciales aprobadas mediante el mecanismo seguro
   existente. No reveles secretos, tokens, cookies, contenido de correo ni adjuntos en logs o Git.
3. No cambies permisos de Entra/Exchange ni la configuración productiva para facilitar la prueba.
   Si el alcance aprobado no permite una comprobación, márcala como bloqueada.
4. Conserva evidencia reproducible para el SHA evaluado y separa lecturas reales, descargas con
   efectos, pruebas offline y verificación posterior al despliegue.

`M365_DOWNLOAD_ROOT` no selecciona una raíz alternativa para las imágenes canónicas. El
almacenamiento vigente es por EAN en `IMAGE_REPOSITORY_ROOT/<EAN>`; sigue el plan de migración
del [repositorio de imágenes](../IMAGE_REPOSITORY.md) antes de cualquier cambio de rutas.
