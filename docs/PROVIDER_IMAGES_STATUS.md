# Proveedores de imágenes: matriz de estado (#59)

Diseño y auditoría: PR #69 (`docs/PROVIDER_INTEGRATIONS_59.md`). La matriz de esta sección refleja pruebas
**offline** con portales y Odoo simulados; no acredita el estado de los servicios en producción.
Verifica el runtime y los recibos por separado según la guía de validación local Cloud.

## Decisión: Cloud es independiente de Odoo

Flujo principal, sin Odoo ni `supplier_ids`: elegir proveedor → pegar/importar EAN (CSV/TXT) → validación y
deduplicación (vista previa sin escribir) → crear solicitudes → exportar lista para el portal / solicitud diferida →
importar ZIP o descarga → identificar vistas → originales al repositorio común (`ImageRepository`) → miniaturas →
pestaña «Biblioteca». «Consultar EAN desde Odoo» es un expander secundario que solo actúa al pulsar el botón. La página
no contacta con Odoo ni con portales al renderizarse.

## Componentes

| Módulo | Función |
|---|---|
| `provider_images.py` | Núcleo: SQLite con máquina de estados, un solo `request` vivo por EAN, eventos append-only, `plan_eans`/`preview_eans`/`parse_ean_text`/`parse_ean_file`, emparejamiento fichero↔EAN con evidencia, importación a `ImageRepository` (SHA-256, originales inmutables, procedencia en `metadata`), revisión con cuarentena, ZIP seguro, límites, backoff. Odoo opcional (`detect`). |
| `marcolin_images.py`, `safilo_images.py`, `nuorder_images.py` | Conectores independientes. |
| `provider_images_ui.py` | Página admin: solicitudes, planificar por EAN (texto/archivo), revisión, biblioteca con miniaturas. |
| `scripts/audit_provider_odoo.py` | Auditoría Odoo solo lectura, opcional. |

Interruptores: `MARCOLIN_/NUORDER_/SAFILO_AUTOMATION_ENABLED` (por defecto `false`). `run_cycle` es `dry_run` por defecto.

## Matriz por proveedor (todo probado solo con simuladores)

| Capacidad | Marcolin | NuORDER | Sáfilo |
|---|---|---|---|
| EAN manual / lista CSV-TXT / validación / deduplicación sin Odoo | ✅ | ✅ | ✅ |
| Solicitud diferida + estados + sin duplicados | ✅ | n/a (manual) | ✅ síncrono |
| Interrupción/respuesta ambigua → `needs_review`, nunca reenvío | ✅ | n/a | ✅ |
| Reintentos seguros | ✅ | ✅ | ✅ |
| Exportar lista de EAN para el portal | ✅ (un código por línea) | ✅ (comas) | ✅ (separada por comas) |
| Subir ZIP (con o sin manifiesto) | ✅ | ✅ | ✅ |
| ZIP sin manifiesto | EAN completo en nombre, si no → revisión | idem | **siempre revisión** |
| Vistas V1/V2/V3/detalle, `unknown` → revisión | ✅ | ✅ | ✅ |
| Biblioteca común + miniaturas + compatibilidad Kering/Luxottica | ✅ | ✅ | ✅ |
| Driver real de portal | ❌ no existe | ❌ no procede | ❌ no existe |
| Requiere credenciales/acceso | portal Marcolin | portal NuORDER | Safilo Omnia |
| Validado contra el portal real | **no** | **no** | **no** |

## Bloqueos reales

- **Marcolin**: sin confirmar método de acceso, términos, MFA/CAPTCHA, estructura del ZIP ni límite real de EAN por
  solicitud (`max_eans_per_request=50` provisional, `batch_size_verified=false`; la UI lo advierte).
  Contrato: `MarcolinDriver`; estados desconocidos del portal → revisión.
- **NuORDER**: la API oficial es OAuth 1.0 iniciada por la marca; no hay API documentada para minoristas. Solo modo
  manual asistido. Acción pendiente: preguntar a NuORDER/las marcas si existe un mecanismo autorizado para nuestra cuenta.
- **Sáfilo**: sin confirmar acceso ni condiciones de automatización, ni si la descarga trae manifiesto. Sin
  manifiesto/metadatos todo va a revisión.
- Odoo (opcional): `supplier_ids` vacío → solo falla «Consultar EAN desde Odoo»; nada más.

## Operación manual autorizada (hoy)

1. Cloud → Proveedores de imágenes → proveedor → «Planificar por EAN» → pegar/subir EAN → «Validar y crear solicitudes».
2. «Solicitudes» → elegir solicitud → copiar la lista de EAN.
3. Persona autorizada entra en el portal con sus credenciales (MFA incluido), pega la lista (Marcolin: solicita imágenes y
   anota el id en «Reconciliar»; Sáfilo Omnia: búsqueda por EAN separados por comas, seleccionar productos, descargar todas
   las vistas; NuORDER: descargar medios) y guarda el ZIP.
4. Subir el ZIP en la solicitud → «Importar ZIP». Lo ambiguo queda en «Revisión».
5. Pestaña «Biblioteca» → «Mostrar imágenes» para comprobar originales y miniaturas.

## Procedimiento de piloto (requiere autorización)

1. Elegir un proveedor y **5–10 EAN** reales conocidos; confirmar por escrito acceso y condiciones del portal.
2. Hacer la operación manual anterior; registrar el tamaño de lote aceptado por el portal y el contenido real del ZIP.
3. Verificar: nº de imágenes por EAN, vistas, SHA-256 de originales en el NAS, miniaturas y visualización en Cloud, y
   que los casos dudosos fueron a revisión.
4. Solo con resultados correctos, valorar un driver real tras el contrato correspondiente y activar un proveedor con
   `pilot_cap` pequeño. Nada de esto se ha hecho.

## Odoo opcional: EAN desde pedidos de compra (`provider_odoo_orders.py`)

Secundario y de solo lectura (`SET TRANSACTION READ ONLY`, timeout de sentencia, límite de líneas 2000 por defecto y
10 000 máximo). Cloud funciona igual sin él; un fallo de Odoo solo muestra un error en ese apartado.

1. **Asociación explícita proveedor Odoo ↔ portal** (`provider_odoo_links.json` en `PROVIDER_DATA_ROOT`, sin secretos).
   Sin asociación confirmada la consulta se rechaza (`asociacion_proveedor_portal_no_configurada`). «Buscar candidatos»
   por nombre solo propone; hay que confirmar los ids y queda registrado el revisor y la fecha. Las marcas son
   informativas: el filtrado por marca requiere conocer el campo real de marca en Odoo y no se ha implementado.
2. **Consulta**: pedidos en estado `purchase` o `done` (se excluyen `draft`, `sent` y `cancel`), contactos comerciales del
   partner agrupados, líneas con producto, `default_code`, `barcode`, proveedor, pedido y fecha, más recientes primero.
3. **Vista previa**: EAN, Referencia, Producto, Proveedor, Pedido, Fecha, Imágenes, Base y Estado (Elegible, Duplicado,
   Requiere revisión por falta de EAN o EAN inválido, Ya completa). La cobertura se lee del catálogo existente con
   `repository_catalog_readonly` (solo lectura, WAL-aware, sin construir `ImageRepository` ni crear directorios o tablas) y
   distingue: *Completas*, *Parciales*, *Sin registros en el catálogo*, *Sin vistas válidas* y *Cobertura desconocida*
   (catálogo inaccesible: nunca se interpreta como imágenes ausentes ni se propone en el lote piloto). «Base» declara si
   procede de metadatos del catálogo (por defecto, archivos no verificados) o de archivos verificados. Un UPC-12 y su
   EAN-13 con cero inicial cuentan como el mismo producto (se conserva el código original). Duplicado = repetido o con
   solicitud en curso.
4. **Lote piloto**: «Proponer lote piloto» preselecciona hasta 10 EAN elegibles sin registros de imágenes,
   con pedidos recientes primero; los parciales quedan en revisión. El usuario ajusta la tabla, marca
   «Apruebo la selección definitiva» y pulsa «Crear solicitudes…».
   Solo se planifican EAN *Elegibles* de la vista previa (da igual lo marcado), mediante `plan_eans` del núcleo; no se
   contacta con ningún portal. Cada selección se anota en `provider_odoo_selections.jsonl` (aprobador, pedido, referencia).
5. Odoo solo prueba el origen del EAN: la imagen descargada sigue necesitando manifiesto, metadatos o revisión.

### MASTERDATA y pedidos Luxottica (cambio local)

- La vista previa MASTERDATA usa el catálogo y el árbol de imágenes existentes en modo de solo lectura.
  Solo se preparan EAN válidos sin registros ni archivos de imagen. Cobertura completa/parcial,
  archivos sin catalogar, solicitudes activas o cobertura no verificable se excluyen o bloquean.
- Tras confirmación, las solicitudes manuales se guardan en el mismo `provider_requests.sqlite`
  que usan los demás conectores; la identidad UPC/EAN deduplica solicitudes activas entre MASTERDATA
  y Odoo. Los orígenes acreditados se conservan de forma idempotente en ese almacén y se muestran
  en el detalle administrativo; registros históricos sin identidad suficiente quedan en revisión.
  Los lotes TXT/manifiesto, de hasta 250 EAN, no contactan con Luxottica.
- Desde pedidos Odoo solo son elegibles los EAN sin registros de imagen. Las líneas parciales o con
  cobertura desconocida no se proponen. La asociación con el pedido conserva evidencia por línea;
  recibos Graph globales no se atribuyen a pedidos por coincidencia de EAN.
- La fecha común inicial de pedidos es `2026-11-01`, persistida en `provider_order_policy.sqlite`
  dentro de `PROVIDER_DATA_ROOT`. Los cambios quedan auditados y no inician reprocesamientos.
  Kering conserva una excepción limitada a los pedidos piloto explícitamente permitidos.
- La preparación, la política de fecha y la restauración de sesión recordada tienen pruebas offline.
  El login recordado requiere `AUTH_COOKIE_SECRET` de al menos 32 caracteres y persistencia del
  almacén de revocaciones. Consulta la evidencia local del SHA para distinguir las pruebas reales
  realizadas de las pendientes; no declarar operativo el monitor por la existencia del código.

### Contrato Odoo contrastado (17/11/2026)

La auditoría real confirmó que `product_supplierinfo.name` enlaza con `res_partner.id`; no existe
`product_supplierinfo.partner_id`. La búsqueda masiva y el script auditor usan ese contrato verificado, sin cambiar
Odoo ni intentar nombres de columna alternativos. `product_id` nulo corresponde a plantilla; una relación específica
de variante no se atribuye a sus hermanas. Modelo y color proceden de `product_template.modelo` y `color_proveedor`,
manteniendo producto, referencia interna, marca y barcode literal.

La corrección se validó en memoria, sin instalar archivos ni desplegar, contra las 28 variantes de la auditoría:
identidad conservada, cero `42703` y el recambio con dos proveedores en revisión. La conexión estaba forzada a solo
lectura y solo ejecutó tres SELECT, además de SET/SHOW. Las pruebas offline usan ahora las columnas reales y cubren
proveedores de variantes hermanas.

Esto acredita consultas, no acceso a portales ni rutas definitivas. Una compra histórica no decide el proveedor de
imágenes; las asociaciones comerciales y los portales siguen separados. Sociedad y vigencia no se confirman por esta
consulta y las relaciones de plantilla siguen siendo evidencia candidata. No se guardaron asociaciones ni se
modificaron solicitudes; el ciclo real de Marcolin, Sáfilo y NuORDER sigue pendiente de autorización en #59.

Rollback de código: revertir la corrección mediante PR; no hay migración de base de datos. Revertir reintroduciría el
`42703` en este Odoo, por lo que la búsqueda debe quedar identificada como no disponible hasta corregirla.

## Originales

`ImageRepository.save` escribe de forma atómica con verificación SHA-256 y sin sobrescribir; el núcleo solo escribe en
el NAS por ahí. Las miniaturas las genera la biblioteca existente (test: original byte a byte intacto).

## Panel de pedidos de solo lectura (P0.1)

En Cloud, un administrador puede abrir `/proveedores-imagenes`, elegir proveedor y fechas y pulsar
**Consultar pedidos de Odoo**. La apertura de la pantalla no consulta Odoo ni portales. La última página y sus
verificaciones quedan en la sesión administrativa; filtrar, paginar la tabla y desplegar el detalle no vuelve a
consultar Odoo. **Consultar siguiente página de Odoo** es una consulta explícita; el resumen y los filtros se
aplican a la página adquirida, no a todos los pedidos del intervalo.

- La consulta acepta un intervalo máximo de 366 días (Madrid), hasta 50 pedidos o 2.000 líneas por página.
  Los pedidos que superan el presupuesto de líneas se pueden continuar en la página siguiente. Su cobertura
  global no se declara completa utilizando solo una parte de sus referencias.
- Se incluyen todos los estados de compra. Solo se buscan proveedores con asociación comercial confirmada.
  Kering también puede usar la asociación de su configuración actual. No se asignan proveedores por marca.
- Las vistas técnicas son frontal, lateral y perspectiva. Se verifica el archivo original mediante checksum y
  huella, y se cuenta una sola vez por foto. Detalle, `lifestyle` y otras fotos no completan vistas técnicas.
  Se distingue un catálogo no disponible de un catálogo sin registros. La verificación de archivos tiene un
  presupuesto por página; los resultados no comprobados se identifican como tales.
- El historial se vincula mediante identidad del pedido en Kering y evidencia de pedido/proveedor/fecha/SKU/EAN
  en solicitudes asistidas. No se vinculan entregas Luxottica por coincidencia de EAN o por recibos globales.
  Cuando no se puede acreditar un vínculo, se muestra **Sin historial disponible**.
- **Abrir imágenes del producto** lleva a la biblioteca en contexto de solo lectura, sin inicializar el almacén
  normal ni escribir miniaturas. Los accesos anteriores permanecen; las herramientas por proveedor se abren
  deliberadamente desde **Herramientas existentes de los proveedores** y no forman parte del recorrido P0.1.

## Acciones desde el pedido (P0.2)

**Mostrar acciones del proveedor** abre los controles del pedido sin iniciar procesos. La preparación
lista los EAN pendientes, vistas sin acreditar y referencias omitidas. Las acciones con efectos
requieren confirmación y un administrador identificado; se vuelve a leer Odoo por ID y se verifica
la cobertura antes de preparar o reintentar referencias. No se crea otro almacén.

- **Kering:** usa el lote manual, el bloqueo y las validaciones del piloto existentes. Conserva el
  pedido completo en el historial pero solo procesa las líneas confirmadas. Un lote acotado no valida
  el pedido completo; las otras referencias pueden prepararse después. Exige modo de prueba y automatización desactivada; no cambia
  configuración, corte ni lista de pedidos autorizados. Muestra progreso persistido y ofrece reintento
  de lotes terminados en error o interrumpidos. No acredita vistas a partir de fotos `lifestyle`,
  de detalle o desconocidas. `35600f2` pertenece a precios (#142); las correcciones de autenticación
  observada están en `a34018b` y `7253a87` (fusionadas en #37), no son prueba del estado productivo actual.
- **Luxottica:** lee recibos locales de monitor, correo y solicitudes; son globales, no evidencia
  de entrega por pedido. No se invoca Graph ni se reenvían solicitudes desde el pedido.
- **Marcolin, Sáfilo y NuORDER:** preparación local manual con evidencia Odoo persistida junto con la
  solicitud, descarga de listados, importación de ZIP y revisión confirmadas. Marcolin también ofrece XLSX
  y grupos diferidos ya registrados. Las solicitudes sin evidencia íntegra siguen en la herramienta
  anterior; no se atribuyen al pedido ni se reenvían. El reintento local conserva la evidencia, excluye
  EAN importados y no reintenta solicitudes con identificador remoto. No se añaden drivers ni permisos.

### Piloto pendiente de P0.1: lectura real de Odoo

Solo con autorización expresa para el entorno y los pedidos: un administrador debe abrir el panel
sin pulsar acciones, elegir un proveedor con enlace confirmado y un intervalo de uno o dos días
que incluya un máximo acordado de 1–3 pedidos. Pulsar exclusivamente **Consultar pedidos de Odoo**.
Comparar contra una lectura autorizada de Odoo: ID/número de compra, contacto y entidad comercial,
línea, variante, código literal (incluidos ceros) y cantidad. En Cloud verificar que la foto y la
vista acreditada corresponden al EAN y que los registros sin vínculo no se atribuyen al pedido.
Anotar fecha, SHA, alcance, conteos y discrepancias anonimizadas; no adjuntar originales ni secretos.
No abrir acciones de proveedor, importar, revisar ni crear solicitudes. No se ha ejecutado este piloto:
queda por verificar el esquema y los campos reales, asociaciones, lectura del NAS e historial disponible.

## Biblioteca y ZIP por pedido (P0.3)

**Imágenes del pedido** muestra el catálogo de la referencia seleccionada. La actualización explícita
vuelve a leer esa referencia del repositorio, no Odoo. La miniatura se prepara en memoria a demanda;
el visor contextual no crea cachés ni inicializa el almacén. Al regresar se recuperan los filtros,
la página consultada, el pedido y la referencia mientras siga activa la misma sesión.

**Preparar exportación de imágenes** verifica las referencias y muestra inclusiones, exclusiones y
vistas no incluidas, sin crear el ZIP. **Generar ZIP verificado** comprueba de nuevo los archivos y
la vigencia del plan. Original no consulta Odoo; Farfetch y Miinto consultan los identificadores
comerciales y verifican la variante en esas dos acciones explícitas, no al navegar. Este recorrido
bloquea ZIP comerciales con alguna vista técnica no acreditada; no cambia los formatos, los nombres
ni las reglas de exportación de la biblioteca anterior. `lifestyle`, `detalle` y `unknown` se pueden
conservar en Original con su procedencia y checksum; no acreditan vistas comerciales.

Se conservan los límites del exportador (64 MiB, 2.000 fotos y 60 s), con un máximo adicional de
200 referencias por lote contextual. Los pedidos fraccionados en la consulta no se ofrecen como
exportación global. Los códigos equivalentes se deduplican sin sustituir el código mostrado en el pedido.

| Funcionalidad anterior | Ubicación unificada | Paridad |
|---|---|---|
| Consulta de pedidos | Panel | Cubierta offline |
| Procesamiento Kering | Acciones del pedido | Piloto y paridad real pendientes |
| Solicitudes Marcolin, Sáfilo y NuORDER | Acciones del pedido | Casos vinculados cubiertos offline; conservar herramientas anteriores |
| Revisión y clasificación | Acciones del pedido y biblioteca | Vista contextual de solo lectura; edición y casos no vinculados conservan su herramienta |
| ZIP Original | Exportación del pedido y biblioteca | Cubierto offline |
| ZIP Farfetch | Exportación del pedido y biblioteca | Cubierto offline; validar identificadores reales |
| ZIP Miinto | Exportación del pedido y biblioteca | Cubierto offline; validar identificadores reales |
| Historial | Detalle del pedido | Evidencia vinculada; recibos Luxottica globales |
| Búsqueda masiva sin pedido | Biblioteca / búsqueda masiva | Se conserva |

#149 propone una segunda consulta en la misma ruta. Sus controles de consulta explícita, límites,
atribución confirmada y claridad sobre la evidencia se conservan en la pila #150–#151; no se
incorpora su segunda pantalla ni se cierra esa PR. No se retira ningún acceso anterior hasta
validar la paridad de extremo a extremo con datos autorizados.

