# Solicitud de imágenes Luxottica

> **Estado operativo:** la preparación de solicitudes es manual y requiere validación/autorización antes de cualquier envío o descarga real. El monitor Graph puede existir en Cloud, pero su estado actual no se ha verificado. No uses instrucciones históricas de subida automática, tareas horarias ni rutas por fecha.

## Almacenamiento canónico

Las imágenes canónicas se guardan por EAN en `IMAGE_REPOSITORY_ROOT/<EAN>`. Los ZIP recibidos
se conservan en `.incoming`, separados del catálogo. `M365_DOWNLOAD_ROOT` no selecciona una
raíz de fotografías distinta. Antes de cualquier migración, seguir el [plan del repositorio](../IMAGE_REPOSITORY.md).

## Preparación desde MASTERDATA

1. Transformar el fichero de origen y revisar sus advertencias.
2. Abrir **Solicitud de imágenes (Luxottica)** y revisar la vista previa.
3. La vista previa consulta el catálogo y los archivos existentes en modo de solo lectura.
   Solo propone EAN válidos sin registros ni archivos de imagen.
4. No son elegibles los EAN con cobertura completa o parcial, solicitud activa, imágenes no
   catalogadas, cobertura no verificable o cualquier estado de revisión. Las filas con el mismo
   EAN o UPC equivalente y datos contradictorios de marca, modelo, color o calibre requieren
   revisión. Si el catálogo, el
   almacén compartido o el historial de lotes no se puede consultar, la preparación se bloquea.
5. Tras confirmación, se registra una solicitud manual en el almacén compartido y se crean
   archivos TXT/manifiesto locales. El lote tiene como máximo 250 EAN.

La app no inicia sesión ni envía solicitudes al portal. Los archivos TXT y su estado local no
son evidencia de envío remoto, recepción ni entrega de imágenes.

## Pedidos Odoo y duplicados

MASTERDATA y Odoo comparten el almacén de solicitudes y la identidad UPC/EAN, para no crear
dos solicitudes activas del mismo producto desde orígenes distintos. Desde pedidos Odoo solo
se proponen EAN sin registros de imagen; la cobertura parcial o desconocida requiere revisión.
La fecha común inicial es `2026-11-01`, configurable desde la administración con registro de
cambios. Cambiarla no reprocesa pedidos. Los pedidos piloto Kering explícitamente permitidos
mantienen su excepción acotada.

## Envío manual autorizado

Una persona autorizada revisa el lote y lo envía manualmente conforme al procedimiento vigente
del portal. No se debe automatizar la carga ni reenviar una solicitud con resultado remoto ambiguo.
La marca de envío en la app solo registra la acción declarada por el operador; debe conservarse
por separado la evidencia autorizada del portal.

## Recepción y Graph

El código de Graph/monitor es independiente del envío de solicitudes. Su presencia en el
repositorio o un contenedor iniciado no demuestra que la configuración, el buzón, OAuth o las
descargas funcionen actualmente. Verifica el runtime, los recibos, los errores y los permisos
siguiendo la [validación local de Cloud](../CLOUD_LOCAL_VALIDATION.md). No ejecutes pruebas de
Graph ni descargues correo real sin autorización específica.

Los scripts y listas de configuración existentes son referencias técnicas, no una autorización
para registrar aplicaciones, conceder permisos, crear tareas programadas o modificar servicios.
No guardes credenciales, correos ni imágenes de producción en Git.

## Checklist por lote autorizado

- [ ] Catálogo, archivos, almacén de solicitudes e historial accesibles y verificados.
- [ ] EAN sin imágenes y sin solicitud activa; parciales y excepciones excluidos.
- [ ] Selección revisada por operador autorizado (máximo 250 EAN).
- [ ] Autorización específica registrada antes de enviar al portal.
- [ ] Evidencia de envío y respuesta conservada fuera de Git.
- [ ] Importación validada en almacenamiento aislado antes de cualquier escritura real.

## Autenticación de la aplicación

La sesión recordada requiere `AUTH_COOKIE_SECRET` de al menos 32 caracteres y un almacén
persistente para la revocación de sesiones. Las pruebas offline verifican la restauración de la
cookie tras recrear la app. La persistencia real tras F5 depende de la configuración efectiva
de Cloud y sigue pendiente de comprobación autorizada; si falta el secreto, la opción **Recordarme**
se deshabilita y el login solo dura en la sesión actual.
