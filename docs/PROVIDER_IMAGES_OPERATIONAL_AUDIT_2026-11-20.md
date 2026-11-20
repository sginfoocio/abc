# Auditoría consolidada de imágenes de proveedores — 2026-11-20

## Alcance y criterio

Auditoría estática de `origin/main` en `56f062fcf266a4c653543a4011cc0f21bbfb65d1`, actualizado desde GitHub el 20/11/2026. Se revisaron los conectores, el repositorio y la biblioteca comunes, pruebas, documentación operativa y las issues/PR pertinentes.

No se accedió a Odoo, correo, portales, contenedores, NAS ni bases de datos productivas. No se hicieron solicitudes, descargas, escrituras, migraciones, cambios de configuración, despliegues ni pruebas contra proveedores. Por tanto, «implementado» o «probado offline» no significa «operativo». Los hechos históricos de producción indicados abajo proceden de informes anteriores y no acreditan el estado actual.

## Resumen ejecutivo

- Existe un almacenamiento canónico compartido (`ImageRepository`) y una biblioteca común. Luxottica y Kering tienen sus flujos propios; Marcolin, NuORDER y Sáfilo reutilizan el núcleo de solicitudes y la importación manual asistida.
- Hay pruebas offline para los cinco proveedores, almacenamiento, biblioteca, Odoo de solo lectura y procesamiento Kering. No existe evidencia reciente que permita declarar operativo en producción ningún proveedor.
- Luxottica y Kering tienen evidencia de operación real histórica; Marcolin tiene evidencia limitada de portal, pero no se ha reconciliado con la solicitud local ni demostrado la importación de su entrega. NuORDER y Sáfilo no tienen piloto real documentado.
- La automatización de nuevos conectores está desactivada por defecto. No se validaron las configuraciones efectivas de producción ni el estado de workers, NAS o copias independientes.
- No se encontró código ni documentación de Thélios. Su pertenencia al alcance vigente debe confirmarse antes de diseñar o anunciar un conector.
- La referencia de Kering del encargo requiere corrección: el SHA `35600f278eeea11b3cb9cf57c3b93fa58c7fda73` es el commit de la PR #142, pero esa PR trata de recuperación de lecturas de precios y no modifica módulos Kering. La afirmación aportada sobre una prueba real de autenticación no se puede validar con esa referencia.

## Matriz de estado

| Proveedor | Estado de implementación | Pruebas / evidencia real | Producción y automatización | Brecha para aceptación |
|---|---|---|---|---|
| **Luxottica** | Implementado: Graph, recepción de correo, manifiestos, monitor e importación al repositorio común. | Pruebas offline; `deployment/nfs/PRODUCTION.md` registra actividad real del monitor y recibos el 12/10. | No verificado actualmente. El despliegue de la app no demuestra la versión/salud del monitor ni una ejecución reciente. | Consultar recibos, errores, configuración efectiva, cobertura y persistencia actuales; revisar `unknown` sin reclasificar por inferencia. |
| **Kering** | Implementado: pedidos, portal, adquisiciones, historial, scheduler, biblioteca y ZIP. La clasificación conserva evidencia y deja sin clasificar señales insuficientes. | Pruebas offline; validación funcional real histórica y limitada descrita en `KERING_IMAGES.md`. La regla posicional documentada solo cubre lateral/frontal/detalle; posiciones 4+ permanecen `unknown` salvo evidencia explícita. | Estado actual no verificado. La afirmación de autenticación del encargo no queda respaldada por la PR/SHA citadas. | Verificar autenticación con la referencia correcta y, con autorización, completar un lote pequeño de descarga/clasificación. Revisar la cobertura sin exigir V2 donde no se ofrece. |
| **Marcolin** | Implementado parcialmente: solicitudes persistentes, exportación, espera, importación asistida, revisión y catálogo común. No hay driver real del portal. | Pruebas offline. La auditoría histórica de la solicitud local nº 1 la clasifica como preparada, sin ID remoto ni envío acreditado. | No verificado; no reenviar ni alterar la solicitud pendiente. Sin automatización autorizada. | Reconciliar evidencia remota sin duplicar la solicitud; validar ZIP, manifiesto, UPC/modelo-color, vistas e importación aislada. #59 sigue abierta. |
| **Sáfilo** | Implementado parcialmente: planificación e importación; archivos sin evidencia inequívoca van a revisión. Sin driver real. | Pruebas offline; no hay lote real documentado. | No verificado; sin automatización autorizada. | Piloto manual autorizado, descarga de todas las imágenes y validación de correspondencia archivo–EAN–vista. #59 sigue abierta. |
| **NuORDER** | Implementado parcialmente: exportación/importación manual asistida. No se presume API minorista. | Pruebas offline; no hay acceso o lote real confirmado. | No verificado; sin automatización autorizada. | Confirmar permisos y procedimiento por grupo/marca; validar correspondencia e importación con lote pequeño autorizado. #59 sigue abierta. |
| **Thélios** | **No implementado / alcance no confirmado**: no se halló código ni documentación específica en el repositorio. | Ninguna prueba o evidencia real encontrada. | No operativo; no hay automatización. | Confirmar que está en el alcance. Si se confirma, especificar portal autorizado, identidad Odoo, contrato de archivos/vistas y adaptador sobre el núcleo común. |

**Conclusión por estado requerido:** ningún proveedor puede declararse *Operativo en producción* ni *Automatización autorizada*. Luxottica y Kering tienen validaciones funcionales históricas; los otros tres conectores no están validados contra sus portales. Thélios no está implementado.

## Hallazgos transversales

### Identidad Odoo y proveedor

`provider_odoo_orders.py` y `scripts/audit_provider_odoo.py` proporcionan consultas opcionales de solo lectura y pruebas sintéticas. La configuración de enlaces es explícita; proveedor, marca y portal no se deducen automáticamente solo del nombre de marca. La consulta de compras no constituye por sí misma prueba de que una variante pertenezca a un portal o de que una imagen corresponda a esa variante. No hay evidencia en esta auditoría de consultas actuales a Odoo; no modificar Odoo.

Faltan una ruta común, confirmada y versionada para producto/variante → proveedor comercial → portal; resolución ante proveedores múltiples; y un piloto autorizado por portal. Las equivalencias UPC/EAN y ceros iniciales están cubiertas por el núcleo de solicitudes, conservando el código literal, pero deben seguir usándose solo como equivalencia de identidad y no como evidencia de proveedor o vista.

### Repositorio, clasificación y cobertura

`ImageRepository` mantiene assets por EAN y SHA-256, representaciones con proveedor/origen/vista/mercado/nombre/fecha/metadatos, huella de píxeles y eventos. Las escrituras son atómicas, las colisiones conservan ambos originales y la reutilización se basa en checksum; la biblioteca consulta las clasificaciones almacenadas. La exportación Original conserva imágenes adicionales y su manifiesto; las exportaciones Farfetch/Miinto mantienen reglas comerciales distintas y no publican automáticamente.

La cobertura de procesamiento Kering se basa en vistas técnicas, no en cantidad de archivos. `detalle` y `unknown` no completan perspectiva. La biblioteca ya muestra y filtra `lifestyle`, pero en la versión auditada la revisión del repositorio lo restringe a Kering y el núcleo de importación de Marcolin/NuORDER/Sáfilo no lo acepta como clasificación explícita. Es una inconsistencia entre taxonomía, revisión y adquisición que debe corregirse sin añadir lifestyle a las vistas requeridas ni contarla como V2.

Los nombres, el orden de ZIP o la similitud visual no acreditan identidad ni vista por sí solos. Las ambigüedades deben conservarse y pasar a revisión; revisar una clasificación no debe renombrar ni modificar los bytes del original.

### Procesamiento y operación

El ciclo no es todavía una única orquestación: Graph de Luxottica, Kering y el núcleo de solicitudes diferidas tienen contratos y persistencia distintos. El núcleo común ya registra estados, errores y eventos para solicitudes Marcolin/NuORDER/Sáfilo y evita reenvíos ambiguos; esto no acredita que el ciclo externo esté validado. La biblioteca, búsquedas y ZIP son locales y no deben iniciar solicitudes a portales.

La documentación de despliegue prohíbe ejecutar el procedimiento sin aprobación y limita el cambio descrito a la aplicación. La evidencia productiva/NAS más reciente contenida en los documentos revisados es histórica (octubre); no se inspeccionó producción en esta auditoría. Deben verificarse por separado el SHA efectivo de app/workers, integridad catálogo–archivos, espacio/permisos NAS, persistencia y copia independiente. Un contenedor activo o un despliegue exitoso no demuestra una ejecución correcta del proveedor.

## Issues y referencias vigentes consultadas

- [#38](https://github.com/sginfoocio/abc/issues/38) permanece abierta. Incluye trabajo pendiente de búsqueda/ZIP por pedido, separación de superficies y verificación funcional/operativa; los avances anteriores no demuestran estado actual de NAS o producción.
- [#59](https://github.com/sginfoocio/abc/issues/59) permanece abierta. El comentario del 14/11 mantiene pendiente la reconciliación de Marcolin nº 1, la validación del ZIP/importación y los pilotos autorizados de Marcolin, Sáfilo y NuORDER.
- No se abrió una issue duplicada: las brechas de Kering y proveedores manuales ya están cubiertas por #38 y #59.
- La PR [#142](https://github.com/sginfoocio/abc/pull/142) se fusionó el 18/11, pero su SHA final modifica componentes de precios y no los conectores Kering. No usarla como evidencia de autenticación Kering. Solicitar el identificador correcto o registrar la validación de autenticación por separado.

## Plan por dependencias y aceptación

| Fase | Trabajo | Dependencia / aceptación |
|---|---|---|
| **A — Auditoría consolidada** | Inventario del código, pruebas, issues, documentación y límites de evidencia. | Esta auditoría cubre código/documentación y GitHub; la inspección actual de producción queda explícitamente pendiente de acceso autorizado. |
| **B — Arquitectura común** | Alinear clasificación opcional, procedencia, identidad y estados sin sustituir el repositorio común. | Lifestyle aceptado solo con evidencia explícita o revisión; no cuenta como vista técnica. Reutilización por checksum y originales intactos. |
| **C — Proveedores** | Cerrar las brechas de los conectores implementados y determinar el alcance Thélios. | Un circuito manual asistido completo es válido si la automatización no está autorizada; no asociar referencias ambiguas. |
| **D — Validación funcional** | Muestras pequeñas, una por proveedor, con datos sintéticos offline y lotes reales autorizados por separado. | Registrar EAN anonimizado cuando corresponda, evidencia archivo–producto–vista, errores/recuperación, SHA y resultado; no confundir simulación con portal real. |
| **E — Cloud** | Validar biblioteca, filtros, revisión, originales y ZIP/manifiestos con compatibilidad de exportación. | Comprobación offline antes de una prueba de navegador autorizada; ninguna publicación externa automática. |
| **F — Preparación productiva** | Verificar workers, NAS, backups, observabilidad, rollback y autorización de automatización. | CI y revisión antes de desplegar; configuración y scheduler permanecen sin cambios hasta aprobación explícita. |

### Criterios para declarar un proveedor terminado

1. Identidad de proveedor/variante sustentada por evidencia; los conflictos no se resuelven por marca, nombre u orden de archivo.
2. Procedimiento autorizado documentado de extremo a extremo, manual asistido o automatizado.
3. Pruebas offline cubren archivos corruptos, ZIP/manifiesto, errores, repetición, interrupción, reintento e idempotencia.
4. Piloto real autorizado demuestra correspondencia archivo–EAN–vista; el lote pequeño queda separado de las pruebas sintéticas.
5. Originales, checksum, procedencia, revisión y exportación verificables; no se pierde ni sobrescribe contenido al repetir.
6. Política comercial de vistas requerida aprobada por proveedor/destino. Lifestyle y detalle no rellenan V1/V2/V3.
7. Operación productiva y autorización de automatización se aprueban por separado; tests y despliegues no las implican.

## Próximos pasos seguros

1. Corregir la referencia PR/SHA de autenticación Kering antes de cerrar esa validación.
2. Implementar la clasificación `lifestyle` como categoría común trazable, opcional y no computable como cobertura técnica; validarla con datos sintéticos.
3. Mantener #38 y #59 como seguimiento de los pilotos/accesos externos; no reenviar la solicitud Marcolin nº 1.
4. Solicitar a un administrador la evidencia de solo lectura de workers/NAS y autorizaciones de portal; sin ella, producción permanece «No verificada».
5. Confirmar si Thélios pertenece al alcance actual antes de iniciar un adaptador.

La referencia adjunta `file_prestashop_prices.py` no se incorpora: corresponde a lectura de precios PrestaShop y no forma parte del ciclo de imágenes.

## Fuentes de código y documentación

- Núcleo y clasificación: [`image_repository.py`](../image_repository.py), [`image_naming.py`](../image_naming.py), [`image_library.py`](../image_library.py), [`image_exports.py`](../image_exports.py), [`image_thumbnails.py`](../image_thumbnails.py).
- Adquisición: [`graph_mail_downloader.py`](../graph_mail_downloader.py), [`monitor_luxottica_downloads.py`](../monitor_luxottica_downloads.py), [`kering_images.py`](../kering_images.py), [`kering_media.py`](../kering_media.py), [`kering_jobs.py`](../kering_jobs.py), [`provider_images.py`](../provider_images.py), [`provider_images_ui.py`](../provider_images_ui.py), [`provider_odoo_orders.py`](../provider_odoo_orders.py), [`marcolin_images.py`](../marcolin_images.py), [`safilo_images.py`](../safilo_images.py), [`nuorder_images.py`](../nuorder_images.py).
- Flujos y almacenamiento: [`bulk_image_search.py`](../bulk_image_search.py), [`repository_storage.py`](../repository_storage.py), [`image_work_storage.py`](../image_work_storage.py), [`market_publication.py`](../market_publication.py).
- Pruebas: `tests/test_image_repository.py`, `tests/test_image_library.py`, `tests/test_provider_images.py`, `tests/test_marcolin_images.py`, `tests/test_safilo_images.py`, `tests/test_nuorder_images.py`, `tests/test_kering_images.py`, `tests/test_kering_viewer_order.py`, `tests/test_provider_odoo_catalog_readonly.py`.
- Operación y alcance: [`KERING_IMAGES.md`](KERING_IMAGES.md), [`IMAGE_REPOSITORY.md`](IMAGE_REPOSITORY.md), [`PROVIDER_IMAGES_STATUS.md`](PROVIDER_IMAGES_STATUS.md), [`PROVIDER_INTEGRATIONS_59.md`](PROVIDER_INTEGRATIONS_59.md), [`MARCOLIN_REQUEST_1_AUDIT.md`](MARCOLIN_REQUEST_1_AUDIT.md), [`PROVIDER_IMAGES_DEPLOYMENT.md`](PROVIDER_IMAGES_DEPLOYMENT.md), [`deployment/nfs/PRODUCTION.md`](../deployment/nfs/PRODUCTION.md).
