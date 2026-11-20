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
- **P0 — Panel único de pedidos e imágenes:** `/proveedores-imagenes` ya reúne accesos por proveedor, pero no ofrece un resumen, tabla de pedidos y detalle comunes. Esa unificación pasa a ser condición de cierre operativo, sin sustituir los procesos existentes.
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

La cobertura de procesamiento Kering se basa en vistas técnicas, no en cantidad de archivos. `detalle` y `unknown` no completan perspectiva. La biblioteca ya muestra y filtra `lifestyle`, pero en la versión auditada la revisión del repositorio lo restringe a Kering, el núcleo de importación de Marcolin/NuORDER/Sáfilo no lo acepta como clasificación explícita y su selector de revisión no ofrece esa opción. Es una inconsistencia entre taxonomía, revisión y adquisición que debe corregirse sin añadir lifestyle a las vistas requeridas ni contarla como V2.

Los nombres, el orden de ZIP o la similitud visual no acreditan identidad ni vista por sí solos. Las ambigüedades deben conservarse y pasar a revisión; revisar una clasificación no debe renombrar ni modificar los bytes del original.

### Procesamiento y operación

El ciclo no es todavía una única orquestación: Graph de Luxottica, Kering y el núcleo de solicitudes diferidas tienen contratos y persistencia distintos. El núcleo común ya registra estados, errores y eventos para solicitudes Marcolin/NuORDER/Sáfilo y evita reenvíos ambiguos; esto no acredita que el ciclo externo esté validado. La biblioteca, búsquedas y ZIP son locales y no deben iniciar solicitudes a portales.

La documentación de despliegue prohíbe ejecutar el procedimiento sin aprobación y limita el cambio descrito a la aplicación. La evidencia productiva/NAS más reciente contenida en los documentos revisados es histórica (octubre); no se inspeccionó producción en esta auditoría. Deben verificarse por separado el SHA efectivo de app/workers, integridad catálogo–archivos, espacio/permisos NAS, persistencia y copia independiente. Un contenedor activo o un despliegue exitoso no demuestra una ejecución correcta del proveedor.

## P0 — Panel único `/proveedores-imagenes`

### Punto de partida y reutilización

La ruta existente en [la aplicación](../app_enhanced.py) selecciona un proveedor y renderiza flujos distintos. No hay un panel global de pedidos ni un detalle operativo compartido. Se ampliará esa ruta en lugar de crear otra pantalla o un segundo motor:

| Proveedor | Reutilizar | Brecha para el panel |
|---|---|---|
| Luxottica | Solicitudes existentes, [Graph](../graph_mail_downloader.py), [monitor](../monitor_luxoptica_downloads.py) y recibos del [panel de procesos](../cloud_dashboard.py). | Mostrar entregas y solicitudes vinculadas por evidencia a pedidos. El panel actual de pendientes de copias de mercado no equivale a pedidos pendientes de imágenes. Mostrar las entregas no conciliadas sin asignarlas por marca ni solo por EAN. |
| Kering | [Consulta y procesamiento de pedidos](../kering_images.py), [UI de pedidos e historial](../kering_images_ui.py) y [scheduler](../kering_jobs.py). | Llevar al detalle común la preparación, autenticación explícita, progreso, resultados y reintentos, sin duplicar ejecutores ni alterar la programación. |
| Marcolin | [Conector diferido](../marcolin_images.py), solicitudes, importación ZIP y revisión del [panel actual](../provider_images_ui.py). | Mostrar identidad remota, espera, entregas e importación junto al pedido. Mantener intacta la solicitud nº 1 mientras no se acredite su identidad remota. |
| Sáfilo | [Conector](../safilo_images.py) y planificación/importación asistida. | Guiar la selección masiva autorizada, revisar correspondencia archivo–EAN–vista e importar en el contexto del pedido. |
| NuORDER | [Conector asistido](../nuorder_images.py) y planificación/importación asistida. | Guiar por grupo/marca confirmados, sin deducir permisos ni automatización; importar y revisar desde el pedido. |
| Thélios | Ninguno acreditado. | Incluirlo solo si se confirma el alcance; no mostrar capacidad operativa supuesta. |

### Consulta, presentación y acciones

- **Resumen superior:** estado de conexión/disponibilidad según evidencia registrada, su origen y antigüedad, última ejecución y resultado, automatización por proveedor y totales de pedidos pendientes, en proceso, completos, parciales y con errores, EAN pendientes, fotos y cobertura. Distinguir conexión no verificada, falta de datos, error, dato antiguo y cero real. Un recibo Graph o un latido no prueba acceso al portal ni entrega. Los totales declaran su período y si la consulta está truncada; no mezclar pedidos, solicitudes, referencias y fotos.
- **Filtros y tabla única:** proveedor, fechas, número de pedido, estado y EAN/UPC. La consulta de Odoo se ejecuta en solo lectura al pulsar **Consultar pedidos**; filtros locales, paginación y apertura del detalle reutilizan los resultados con alcance, fecha de consulta y límite visibles. Cambiar parámetros que amplían el alcance requiere una nueva consulta explícita. Reutilizar los contratos de [pedidos por proveedor](../provider_odoo_orders.py), [búsqueda masiva](../bulk_image_search.py) y [lectura de catálogo](../repository_catalog_readonly.py), con una capa de consulta y presentación sobre los almacenes existentes, sin migrarlos ni fusionarlos.
- **Identidad, estados y cobertura:** usar el ID del pedido y de la línea/variante de Odoo al conciliar; conservar EAN/UPC literal y equivalencias acreditadas. No atribuir una solicitud o intento a un pedido por un EAN coincidente. Mostrar por separado el estado de compra de Odoo, el estado operativo normalizado y el estado original con su fuente. Contar referencias con cobertura completa, parcial, sin imágenes o desconocida; no tratar fallos del catálogo como ausencia. Separar número de fotos de cobertura frontal/lateral/perspectiva. Detalle, `lifestyle` y desconocidas no completan vistas técnicas. Mantener la política vigente mientras no se apruebe otra.
- **Detalle del pedido:** proveedor, número, fecha, estados, total de referencias y EAN únicos; por línea, producto, modelo, color y calibre si están acreditados, fotos disponibles, vistas técnicas presentes y pendientes y fotos adicionales. Mostrar último intento, progreso, historial y errores de cada proveedor. Ofrecer navegación contextual a la biblioteca filtrada por pedido/EAN y a la exportación existente, sin adquirir ni publicar imágenes al navegar.
- **Acciones y permisos:** desde el pedido, consultar, previsualizar y confirmar la preparación, procesar manualmente, reintentar, importar ZIP, revisar y exportar según permisos, estado y capacidades acreditadas. La selección y la previsualización no generan trabajo. Antes de ejecutar, volver a comprobar permisos, cobertura, trabajo vivo y estado en el servicio de origen para evitar duplicados, incluso tras reruns o selecciones concurrentes. El modo automático solo se refleja o usa cuando ya está configurado y autorizado; el panel no lo activa. Mantener la configuración en una sección administrativa separada. No invocar Odoo, portales ni acciones mutantes por abrir, actualizar el navegador o cambiar filtros; la consulta local previa debe ser acotada y de solo lectura.

### Implantación progresiva y pruebas

| Entrega | Alcance y criterio de paso |
|---|---|
| **P0.1 — Lectura común** | Resumen local y tabla/detalle de solo lectura sobre datos sintéticos, con consulta Odoo explícita y paginación. Probar todos los estados, el recuento por pedido/referencia/EAN, equivalencias con ceros, ambigüedades, errores de fuente y datos truncados. Verificar cero llamadas externas y cero escrituras en carga, rerun y navegación. |
| **P0.2 — Operación por proveedor** | Reutilizar acciones de Kering/Luxottica primero y después integrar las asistidas de Marcolin, Sáfilo y NuORDER. Probar permisos en UI y servidor, confirmación, concurrencia, idempotencia, historial intacto y que el fallo de un proveedor no oculta ni bloquea a los demás. Las capacidades sin autorización o evidencia se muestran como no disponibles, con motivo. |
| **P0.3 — Navegación y aceptación** | Integrar biblioteca y exportación contextual; no retirar accesos anteriores hasta probar paridad y conservar enlaces existentes. Verificar interfaz en escritorio y móvil con datos sintéticos y ejecutar regresiones por proveedor. Para declarar cerrado el P0, un administrador debe poder localizar un pedido pendiente, inspeccionar cobertura, ejecutar el procedimiento autorizado, revisar resultados y abrir/exportar las imágenes desde el panel para cada proveedor incluido. Los pilotos reales requieren autorización separada y se registran aparte de las pruebas offline. |

**Coordinación:** [#147](https://github.com/sginfoocio/abc/pull/147) contiene esta auditoría y el plan; no implementa la pantalla. Las entregas están en [#150](https://github.com/sginfoocio/abc/pull/150) (consulta), [#151](https://github.com/sginfoocio/abc/pull/151) (acciones) y [#152](https://github.com/sginfoocio/abc/pull/152) (biblioteca y ZIP), apiladas en ese orden. Su implementación offline no acredita integración en `main`, despliegue ni paridad productiva. [#146](https://github.com/sginfoocio/abc/pull/146) sigue separada: amplía `lifestyle` sin acreditar vistas técnicas. Mantener [#38](https://github.com/sginfoocio/abc/issues/38) y [#59](https://github.com/sginfoocio/abc/issues/59) abiertas para validaciones reales.

El borrador [#149](https://github.com/sginfoocio/abc/pull/149) añade otra tabla en la misma ruta y
entra en conflicto con la integración de la pila. Ambos enfoques exigen consulta explícita y
asociación confirmada, conservan líneas repetidas y distinguen cobertura desconocida. #149 excluye
pedidos no confirmados, permite fijar manualmente el tope de líneas y usa solo metadatos de catálogo;
la pila ofrece filtro de estado, paginación con presupuesto fijo y contraste de archivos, además de
historial, acciones y biblioteca. Sus cuatro pruebas cubren agregación, asociación, ausencia de I/O
implícita y catálogo desconocido; la pila prueba esos contratos y el recorrido ampliado.
Recomendar sustituir el borrador solo cuando se acepte que el tope editable y la vista de metadatos
sin verificar no son requisitos de operación. No cerrar ni fusionar el borrador automáticamente.

## Issues y referencias vigentes consultadas

- [#38](https://github.com/sginfoocio/abc/issues/38) permanece abierta. Incluye trabajo pendiente de búsqueda/ZIP por pedido, separación de superficies y verificación funcional/operativa; los avances anteriores no demuestran estado actual de NAS o producción.
- [#59](https://github.com/sginfoocio/abc/issues/59) permanece abierta. El comentario del 14/11 mantiene pendiente la reconciliación de Marcolin nº 1, la validación del ZIP/importación y los pilotos autorizados de Marcolin, Sáfilo y NuORDER.
- No se abrió una issue duplicada: las brechas de Kering y proveedores manuales ya están cubiertas por #38 y #59.
- La PR [#142](https://github.com/sginfoocio/abc/pull/142) se fusionó el 18/11, pero su SHA final modifica componentes de precios y no los conectores Kering. No usarla como evidencia de autenticación Kering. Solicitar el identificador correcto o registrar la validación de autenticación por separado.

## Plan por dependencias y aceptación

| Fase | Trabajo | Dependencia / aceptación |
|---|---|---|
| **A — Auditoría consolidada** | Inventario del código, pruebas, issues, documentación y límites de evidencia. | Esta auditoría cubre código/documentación y GitHub; la inspección actual de producción queda explícitamente pendiente de acceso autorizado. |
| **B — Arquitectura común** | Alinear clasificación opcional, procedencia e identidad; iniciar P0.1: resumen, consulta y detalle de solo lectura. | No sustituir el repositorio ni los almacenes; `lifestyle` no cuenta como vista técnica. P0.1 no espera pilotos reales ni la fusión de #146. |
| **C — Proveedores** | Cerrar brechas de los conectores y determinar el alcance Thélios. P0.2 integra acciones sobre los servicios existentes. | Un circuito manual asistido completo es válido si la automatización no está autorizada; no asociar referencias ambiguas. |
| **D — Validación funcional** | Muestras pequeñas, una por proveedor, con datos sintéticos offline y lotes reales autorizados por separado. | Registrar EAN anonimizado cuando corresponda, evidencia archivo–producto–vista, errores/recuperación, SHA y resultado; no confundir simulación con portal real. |
| **E — Cloud** | P0.3: acceso contextual a biblioteca y exportación; validar filtros, revisión, originales y ZIP/manifiestos. | Comprobar paridad antes de retirar accesos duplicados. Ninguna publicación externa automática. |
| **F — Preparación productiva** | Verificar workers, NAS, backups, observabilidad, rollback y autorización de automatización. | CI y revisión antes de desplegar; configuración y scheduler permanecen sin cambios hasta aprobación explícita. |

### Criterios para declarar un proveedor terminado

1. Identidad de proveedor/variante sustentada por evidencia; los conflictos no se resuelven por marca, nombre u orden de archivo.
2. Procedimiento autorizado documentado de extremo a extremo, manual asistido o automatizado.
3. Pruebas offline cubren archivos corruptos, ZIP/manifiesto, errores, repetición, interrupción, reintento e idempotencia.
4. Piloto real autorizado demuestra correspondencia archivo–EAN–vista; el lote pequeño queda separado de las pruebas sintéticas.
5. Originales, checksum, procedencia, revisión y exportación verificables; no se pierde ni sobrescribe contenido al repetir.
6. Política comercial de vistas requerida aprobada por proveedor/destino. Lifestyle y detalle no rellenan V1/V2/V3.
7. Operación productiva y autorización de automatización se aprueban por separado; tests y despliegues no las implican.
8. El recorrido operativo de ese proveedor es accesible desde el panel único, sin perder permisos, historial ni protecciones de duplicados.

## Próximos pasos seguros

1. Revisar la pila en orden `main → #150 → #151 → #152`. Tras cada fusión autorizada, comprobar
   el SHA y CI de `main`, reorientar únicamente la siguiente PR y revisar su diff antes de continuar.
   Si se fusiona por squash, comprobar que el rango de commits de la fase anterior no vuelve a entrar
   (preparar un `rebase --onto` con base antigua explícita, sin publicarlo sin autorización).
2. Revisar #146 como cambio independiente de `lifestyle` y corregir la referencia PR/SHA de autenticación Kering antes de cerrar esa validación.
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
