# Solicitud de imágenes Luxottica

> **Activación en Cloud:** el servicio de envío desde pedidos se instala por separado y su configuración inicial está desactivada. La instalación y la verificación real en `s2026` son necesarias antes de habilitarlo.

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

Los lotes preparados desde Masterdata conservan su flujo manual. Los archivos TXT y su estado
local no son evidencia de envío remoto, recepción ni entrega de imágenes.

## Pedidos Odoo y duplicados

MASTERDATA y Odoo comparten el almacén de solicitudes y la identidad UPC/EAN, para no crear
dos solicitudes activas del mismo producto desde orígenes distintos. Desde pedidos Odoo solo
se proponen EAN sin registros de imagen; la cobertura parcial o desconocida requiere revisión.
La fecha común inicial es `2026-11-01`, configurable desde la administración con registro de
cambios. Cambiarla no reprocesa pedidos. Los pedidos piloto Kering explícitamente permitidos
mantienen su excepción acotada.

Al preparar desde Masterdata se registra el nombre del archivo y la posición de la fila en la
tabla transformada, junto con el código literal y su identidad. Si el mismo código aparece en un
pedido confirmado, la preparación del pedido puede añadir su evidencia a la solicitud activa
sin cambiar su estado ni reenviarla. En sentido inverso, un administrador puede confirmar
«Asociar Masterdata a solicitudes existentes» tras revisar la vista previa. La asociación exige
modelo, color y calibre coincidentes y una variante de pedido acreditada; si faltan datos o hay
contradicciones queda para revisión. Los registros históricos sin identidad suficiente no se
atribuyen por coincidencia de EAN. Los orígenes, fecha de registro y revisor pueden consultarse
en el detalle administrativo de la solicitud. Una vista previa no escribe.

## Envío manual autorizado

Una persona autorizada revisa y envía los lotes de Masterdata. La marca de envío manual en la app
registra la acción declarada por el operador y requiere evidencia aparte.

## Envío programado desde pedidos de compra

- El servicio `abcd-luxottica-sender` usa pedidos confirmados o completados del proveedor 570, desde
  el inicio común, con mínimo 01/11/2026. El lector usa PostgreSQL en solo lectura. Se excluyen
  imágenes presentes o no verificables, identidades dudosas y EAN/UPC equivalentes ya solicitados.
- En **Configuración → Essilor Luxottica**, un administrador puede cambiar el usuario y la
  contraseña, el correo de recepción, el intervalo y el estado del envío. La contraseña nunca
  se muestra ni se guarda en el repositorio. El archivo cifrado vive en el volumen de proveedores
  y el servicio comparte su clave con la aplicación. Usa `LUXOPTICA_ENCRYPTION_KEY_FILE` o la clave
  de configuración existente del servicio. El usuario y la contraseña vigentes del entorno se
  usan al abrir la configuración por primera vez.
- Se procesa un lote de hasta 10 EAN por ciclo, con el límite diario del almacén de solicitudes.
  El archivo se escribe como texto ASCII sin cabecera, con un código por línea y finales CRLF.
  Si el portal queda en «Ver tipo» sin opciones tras cargarlo, el proceso se detiene sin cambiar
  a entrada manual. Antes de cambiar el fichero o reintentar, registra el paso y comprueba la
  sesión, el formato y la respuesta del portal. El proceso acepta el botón «Aceptar» únicamente
  cuando identifica el aviso de cookies por su texto y encuentra un único botón en su contenedor;
  si falta o es ambiguo, se detiene. Tras revisión
  y autorización del usuario el 25/11/2026, acepta el aviso legal de descarga solo si coincide
  íntegramente con la huella SHA-256 del texto revisado. Si cambia, falta el botón o aparecen
  otros avisos legales o documentos para firmar, se detiene para revisión humana; no los pospone.
- El destinatario adicional debe guardarse en el formulario del portal y aparecer después de
  cerrar la edición. Si no se puede comprobar, no se envía la solicitud.
- Se registra «Enviada» únicamente tras observar una confirmación explícita en el portal. Un
  resultado incierto, incluido un reinicio durante el envío, conserva el lote activo en revisión
  y bloquea nuevos envíos automáticos. Antes de reintentar, comprueba en el portal si el lote se
  aceptó y reconcilia su estado de forma explícita.
- Verifica el acceso y la pantalla previa con `python -B luxottica_order_scheduler.py --preflight-file ARCHIVO.txt`,
  en el entorno autorizado y con el envío automático desactivado. Este modo carga el archivo
  indicado sin reescribirlo (hasta 250 códigos), no pulsa el botón de envío y no crea una
  solicitud en Cloud. Verifica por separado que un envío autorizado
  produce una confirmación real antes de habilitar el alcance general.
- El despliegue protegido solo actualiza `abcd-app`. La instalación del remitente y la
  actualización del monitor requieren mantenimiento de sus servicios con la imagen de la misma
  revisión, el almacén de proveedores compartido y los montajes verificados. Para detener nuevos
  envíos, desmarca la activación en **Configuración**. Comprueba en Cloud cualquier lote que
  hubiese iniciado el envío.
- El remitente monta el directorio del catálogo en `/app/image-state`, en solo lectura.
  Configura `IMAGE_REPOSITORY_STATE_HOST_ROOT` con el directorio local efectivo del catálogo
  cuando esté separado de las imágenes; si se omite, usa `IMAGE_REPOSITORY_HOST_ROOT`
  (por defecto `./image_repository`). El directorio debe existir: Compose no lo crea para
  el montaje del catálogo.
  Monta el directorio completo, no solo el SQLite, para incluir sus archivos WAL y SHM cuando
  existan. Verifica una lectura coherente sin inicializar el catálogo antes de activarlo. El
  Compose privado de producción requiere la misma configuración y comprobación por separado.
- Si las imágenes están bajo un montaje NFS, configura `IMAGE_REPOSITORY_MOUNT_HOST_ROOT`
  con la raíz de la exportación en el host, `IMAGE_REPOSITORY_MOUNT_ROOT` con su destino en el
  contenedor, `IMAGE_REPOSITORY_ROOT` con el directorio `images` dentro de ese montaje y
  `IMAGE_REPOSITORY_NFS_SOURCE` con la exportación esperada. El remitente monta la exportación
  completa en solo lectura; la validación de lecturas exige igualmente la exportación exacta y
  NFS `hard`. Los servicios que escriben siguen requiriendo un montaje escribible.

## Recepción y Graph

El código de Graph/monitor es independiente del envío de solicitudes. Su presencia en el
repositorio o un contenedor iniciado no demuestra que la configuración, el buzón, OAuth o las
descargas funcionen actualmente. Verifica el runtime, los recibos, los errores y los permisos
siguiendo la [validación local de Cloud](../CLOUD_LOCAL_VALIDATION.md). No ejecutes pruebas de
Graph ni descargues correo real sin autorización específica.

El monitor conserva el ZIP y valida tamaños y límites de descompresión. Para los envíos nuevos,
puede resolver el EAN desde la evidencia de pedido de una solicitud cuyo envío tenga confirmación,
además de los manifiestos anteriores. Si el mismo modelo y color tienen códigos distintos,
apuntan a varias variantes de Odoo o falta la identidad de la variante, conserva el archivo
para revisión. La confirmación de envío y la importación
al NAS son comprobaciones distintas. El monitor solo marca el correo como procesado tras
completar su tratamiento. Un original importado por coincidencia de modelo y color no acredita
por sí solo la entrega de un identificador de lote remoto.

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
