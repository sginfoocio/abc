# Repositorio comun de imagenes por EAN

## Galeria: fotografias y representaciones (17/11/2026)

La biblioteca y el detalle de busqueda masiva muestran una fotografia por
`(EAN literal, checksum)`, no una miniatura por representacion. Los contadores
separan fotografias unicas de representaciones: 16 o 40 representaciones pueden
seguir siendo ocho fotografias. Son recuentos del catalogo, no un inventario
fisico exhaustivo del NAS ni garantia de que todos los archivos sean accesibles.
La visualizacion mantiene los avisos existentes para archivos no disponibles.

Todos los registros originales permanecen en el catalogo y en la trazabilidad
administrativa: proveedor, origen, EAN/UPC, modelo/color disponibles, vista,
checksum, huella y representaciones. La seleccion de una representacion para la
miniatura no cambia la clasificacion guardada.

Archivos de checksum diferente se conservan aunque tengan la misma vista o
huella visual. La biblioteca avisa de huellas iguales entre archivos distintos y
de checksum/huella compartidos entre EAN; estas coincidencias son candidatos a
revision de variante/color, no prueba de que una asociacion sea incorrecta.
No se consulta Odoo al navegar, no se fusionan EAN y no se reclasifica ni borra.

El ZIP individual existente no cambia: conserva sus nombres y alias y
solo se genera al pulsar. El ZIP conjunto descrito abajo es una descarga
independiente. Una seleccion comercial persistente y revisada manualmente sigue
siendo una propuesta separada, no una reclasificacion implicita:
[validacion de proveedores y reconstruccion futura](IMAGE_PROVIDER_VALIDATION_AND_REBUILD.md).

## Busqueda local multiple y ZIP conjunto

En **Repositorio de imagenes**, abrir **Buscar varios EAN y descargar ZIP**.
Admite un codigo o varios separados por comas, punto y coma, espacios, tabuladores
o saltos de linea; tambien CSV/TXT UTF-8 y Excel `.xlsx` (hoja activa).
Reutiliza la validacion y agrupacion GTIN de `bulk_image_search.group_inputs`
(#131) y las fotografias unicas y trazabilidad de `image_library` (#138).
No ejecuta el analisis de pedidos/proveedores de la pagina de busqueda masiva,
ni inicia solicitudes, conectores, recepcion, publicacion o descargas externas.

Los codigos se mantienen como texto. UPC/EAN equivalentes se agrupan por la
identidad GTIN existente, conservando el primer literal introducido, incluidos
sus ceros, para la seleccion y carpeta de descarga. En Excel, usar texto o
formato numerico de ceros (`0000000000000`); no es posible recuperar ceros que
Excel ya haya eliminado. No se admiten formulas ni decimales. Puede emplearse
una columna EAN/Barcode/GTIN, o una lista sin cabecera; Excel tambien reconoce
UPC. No se importa Master Data ni se cambia la fecha de ningun pedido.

El resumen separa repetidos, invalidos y codigos sin imagenes en el catalogo
local; esto ultimo no significa que el producto no exista en Odoo.
La tabla muestra EAN solicitado, EAN del catalogo, modelo, color disponible,
vistas, proveedor, fotos unicas, representaciones y coincidencias a revisar.
La galeria inferior queda limitada al listado y mantiene todos sus filtros
y el detalle existente. **Mostrar todo el catalogo** retira esta limitacion.

Se pueden descargar todos los EAN encontrados o una seleccion. Los filtros de
la galeria no cambian silenciosamente esa seleccion del ZIP. Cambiar entrada,
seleccion, formato o actualizar el catalogo invalida la descarga preparada.
No se genera un ZIP durante la navegacion ni antes de pulsar **Preparar ZIP
conjunto**; **Descargar ZIP conjunto** entrega esa instantanea verificada.

| Formato | Carpetas/nombres y reglas conservadas |
| --- | --- |
| Original | `<primer EAN literal>/<nombre original saneado>`. Una foto por SHA-256 e identidad GTIN, preferentemente con nombre de representacion original. Incluye todas las vistas, detalles, lifestyle y desconocidas; no deduplica por vista ni por similitud visual. |
| Farfetch | `Farfetch/<EAN>/<ID externo>_V1.png`, `_V2.png`, `_V3.png`. Reutiliza el plan de publicacion existente: PNG se conserva, JPEG/WEBP admitidos se convierten en la copia PNG sin reducir resolucion. |
| Miinto | `Miinto/<EAN>/<ID externo>_V1.jpeg`, `_V2.jpeg`, `_V3.jpeg`. Conserva PNG bajo extension `.jpeg`, como el contrato historico, y JPEG sin recodificar. WEBP sigue sin conversion aprobada. |

Las reglas de nombre originales estan en
`graph_mail_downloader._market_image_names` e
`image_naming.market_view_from_image_name`: `noshad__fr` -> V1,
`noshad__qt` -> V2, `shad__lt` -> V3. El plan compatible de vistas/formatos
esta en `market_publication.create_publication_plan`; la descarga nueva lo
reutiliza sin llamar a `publish_market_images` ni escribir copias permanentes.
El proceso anterior de copias pendientes sigue accesible en **Pendiente
Luxoptica**, y el publicador CLI sigue siendo una operacion distinta y manual.

Los IDs comerciales **no son el EAN, SKU, modelo ni color**. Solo al preparar
Farfetch/Miinto se leen de `diagonal_product_website.product_website_id`,
relacionado por `product_id = product_product.id`; `siteweb_id = 3` significa
Farfetch y `2` Miinto. `read_export_market_ids` usa transaccion PostgreSQL
`READ ONLY`, timeout SQL de 20 segundos, rollback y limite de 5000 filas.
La consulta nueva conserva esas relaciones existentes y detecta varias
variantes por codigo o varios IDs distintos para el canal; no elige el ultimo
ni inventa un sustituto. La navegacion, busqueda y formato Original no necesitan
Odoo. La disponibilidad/esquema de esta consulta se prueba offline, no constituye
una validacion nueva de los IDs reales de produccion.

Farfetch/Miinto conservan la politica existente de vistas acreditadas
Luxoptica/Kering y revisiones Kering; no se amplian por inferencia a otros
proveedores. Detalles, lifestyle, vistas no acreditadas, alternativas de la
misma vista y formatos no aprobados se enumeran como exclusiones explicitas
antes de descargar y en `manifest.json`, de acuerdo con la decision funcional
de esta fase. Original permite conservar todas las fotos de cualquier proveedor.
Si algun EAN seleccionado carece de ID inequívoco o de toda foto publicable,
no se entrega un ZIP parcial: se muestra el error y puede retirarse ese EAN o
elegirse Original. No se cambia ninguna regla de renombrado para las adicionales.

Original resuelve colisiones, tambien de mayusculas/minusculas, mediante el
checksum completo. En formatos especiales no se añaden sufijos nuevos: las
carpetas separan los EAN y una colision real dentro del mismo EAN bloquea el ZIP.
Un ID repetido entre EAN puede producir nombres base iguales, pero no rutas
duplicadas ni mezcla de variantes. El manifiesto conserva EAN, modelo/color
disponibles, SHA de origen/salida y todas las representaciones relacionadas,
con proveedor, origen, vista, mercado, nombre e identificador.

Por seguridad, checksum o huella compartidos con otro GTIN distinto, o metadatos
de modelo/color contradictorios dentro de una identidad, bloquean la seleccion
completa. Son sospechas, no una reclasificacion automatica ni prueba de cual
asociacion es incorrecta. Deben revisarse por separado; no hay override automatico.
Los GTIN equivalentes con ceros iniciales no se consideran otros colores.
Archivo ausente, corrupto, cambiado o catalogo modificado durante la preparacion
tambien producen error sin entregar un ZIP parcial.

Limites por operacion: 500 entradas, 1 MiB por listado; Excel descomprimido
16 MiB/2000 entradas ZIP, 500 filas de datos y 50 columnas; 2000 fotografias
unicas, 64 MiB por ZIP incluido manifiesto, 30 MiB/25 MP por foto.
El ZIP usa almacenamiento temporal con spool de 8 MiB y borra sus temporales
al terminar o fallar; Streamlit conserva una unica descarga conjunta preparada
por sesion, hasta 64 MiB. La verificacion/recodificacion trabaja con archivos
limitados, sin generar copias permanentes ni reducir la calidad del original.
El presupuesto de 60 segundos se comprueba entre lecturas y antes de entregar
el archivo: no interrumpe por la fuerza una E/S de NAS o conexion bloqueada
en el sistema operativo. No se debe interpretar como un timeout duro del NFS.

### Validacion y activacion separadas

Pruebas offline: `tests/test_repository_multi_export.py`,
`tests/test_image_library.py` y regresiones existentes de repositorio, busqueda,
publicacion y descargas Kering. Cubren 16/40 representaciones -> ocho fotos,
ceros, entrada repetida/invalida/inexistente, variantes/colores, los tres
formatos, integridad, conversion, colisiones, limites, errores y permisos.
La prueba de navegador debe usar exclusivamente catalogo y Odoo sinteticos:
buscar dos EAN, preparar/descargar los tres formatos, revisar ZIP/manifiesto,
probar subida CSV/XLSX y ancho movil, sin acceso a portales ni NAS real.

Tras revision y autorizacion de fusion, exigir CI del HEAD definitivo y de
main. El despliegue de la aplicacion requiere otra autorizacion y debe utilizar
el procedimiento Cloud vigente, verificando SHA, montajes y acceso administrativo.
Validar primero en staging con una muestra autorizada y existentes en el
repositorio; Original no debe emitir SQL y los especiales solo SELECT de IDs,
sin pedidos ni solicitudes. Verificar tambien descarga individual y filtros.
Rollback: volver a la imagen anterior de la aplicacion, sin restaurar ni migrar
catalogo, pues esta mejora no cambia su esquema ni archivos originales.
**No actualizar ni reiniciar el worker Kering como parte de esta mejora**:
su plan independiente sigue en
[KERING_WORKER_RELEASE.md](../deployment/cloud/KERING_WORKER_RELEASE.md).

## Inventario de contratos previo a cambiar rutas

Revision de `main` 25a6252 (2026-10-10), sin acceso ni migracion en Cloud.
Las integraciones de imagenes presentes son Luxoptica/Graph y Kering.
Adyen exporta informes, no fotografias. No existe sincronizador NAS en el codigo
actual: cualquier tarea externa debe inventariarse antes del cambio en Cloud.

| Superficie | Contrato vigente |
| --- | --- |
| Original Luxoptica | `_sanitize_filename` conserva nombre y extension; sustituye `\ / : * ? " < > \|` por `_`. No recodifica imagenes. |
| Modelo Luxoptica | `_model_from_image_name`: texto anterior a `__`, elimina un cero inicial; solo afecta al directorio antiguo. |
| Identificacion Luxoptica | `_image_model_color_key` normaliza modelo/color, elimina `_NNNA` del color; manifiestos resuelven solo combinaciones con un EAN unico. |
| Vistas Luxoptica | `_market_view_from_image_name`: `noshad__fr` -> V1 (frontal), `noshad__qt` -> V2 (perspectiva), `shad__lt` -> V3 (lateral). Otras vistas no acreditan esas tres. |
| Farfetch | Solo originales `.png`, `<id>_V1.png`, `<id>_V2.png`, `<id>_V3.png`. |
| Miinto | Solo originales `.png`, `<id>_V1.jpeg`, `<id>_V2.jpeg`, `<id>_V3.jpeg`. Los bytes siguen siendo PNG: **no corregir ni recodificar** como parte de esta migracion. |
| Kering anterior | `ImageStore.path`: `<vista>.img`, bytes originales JPEG/PNG/WEBP, sin recodificar. Estos archivos existentes nunca se renombran por unificar o exportar. |
| Kering nuevo | Solo una vista acreditada antes de renombrar recibe `<modelo>__<color>__noshad__fr/qt` o `shad__lt`, con extension original. Sin evidencia conserva el nombre de origen saneado. |
| ZIP actual | `<EAN>/<nombre solicitado>` de todas las representaciones validas, incluidas extensiones historicas `.img` y de mercado, mas `manifest.json`; sin proveedor en carpetas. |
| Galerias | Luxoptica deducia modelo/EAN/mercado de carpetas; Kering usa `valid_views` y snapshots de pedidos por EAN. |
| Historial | Kering `history.sqlite3`: runs, attempts, snapshots/results con EAN; configuracion cifrada independiente. Graph `.mail_download_state.json` y `.market_pending.json`. |
| Colisiones | El repositorio canonico conserva contenidos distintos con nombres de colision deterministas. La publicacion comercial rechaza destinos ocupados con otros bytes. |

Las funciones de nombre anteriores se mantienen. La ubicacion nueva es
`IMAGE_REPOSITORY_ROOT/<EAN>/<nombre>`, nunca por pedido, modelo o proveedor.
Si dos contenidos distintos necesitan el mismo nombre, el primero permanece y
la alternativa usa `stem__<sha256 completo>.extension`; se registra el conflicto
y el nombre solicitado. No se renombra el primero ni se pierde ninguno.
Los nombres de mercado identicos en bytes son representaciones/alias: mantienen
su nombre y extension en metadatos y exportaciones, sin otra copia fisica.

## Publicacion comercial separada

`market_publication.py` prepara copias de canal sin escribir en el repositorio
canonico ni cambiar su catalogo: consulta SQLite con `mode=ro` y falla si el
catalogo existente no puede abrirse en solo lectura. El origen aprobado es
`/mnt/cloud-imagenes/images/`; la raiz local aprobada es
`/mnt/cloud-imagenes/market-publication/`. Los nombres son
`Farfetch/<EAN>/<ID>_Vn.png` y `Miinto/<EAN>/<ID>_Vn.jpeg`. Miinto conserva los
bytes PNG heredados bajo `.jpeg` y copia JPEG sin recodificar. Farfetch recibe
PNG; fuentes Kering JPEG/WEBP se convierten solo en esa copia. WEBP no se envia
a Miinto hasta aprobar expresamente su conversion. El manifiesto local conserva
EAN, canal, vista y checksums de origen y salida, sin copiar metadatos canonicos
ni URLs.

El comando es manual y usa Odoo solo para leer los IDs comerciales. Por defecto
simula y no crea la raiz de destino; `--apply` es necesario para materializar
copias locales, y exige `--expected-plan-sha256` con la firma de una simulacion
anterior identica. Una colision con bytes distintos o un manifiesto ajeno detiene
el plan sin sobrescribirlos; las salidas antiguas registradas en el manifiesto
se señalan y bloquean una nueva aplicacion hasta su revision manual, sin borrar
archivos. Las vistas desconocidas, de detalle, duplicadas,
con pixeles repetidos o con conflictos Kering sin revision quedan excluidas.
La secuencia es `python market_publication.py`, revisar `plan_sha256` y despues
ejecutar el mismo comando con `--apply --expected-plan-sha256 <firma>`, solo
cuando exista autorizacion para materializar la copia local. No ejecuta rsync
ni crea tareas, timers o servicios. Cualquier segunda
sincronizacion al destino remoto
`rsync://sync_images@192.168.0.19/Fotos/market-publication/` requiere
validacion y autorizacion de produccion separadas; debe excluir el lock local
`.market-publication.lock` y los temporales `.market-publication-*.tmp`. El
espejo de originales permanece independiente.

## Configuracion y operaciones

`IMAGE_REPOSITORY_ROOT` es la unica raiz de fotografias y bloqueos compartidos.
Por defecto es `repo/images` relativo al proyecto (no al directorio de trabajo).
En Cloud usar un volumen persistente comun, montado en **app, monitor Graph y
programador Kering**. `KERING_DATA_ROOT` conserva configuracion e historial, no
fotografias nuevas. `M365_DOWNLOAD_ROOT` deja de controlar fotografias.

Para almacenamiento separado, `IMAGE_REPOSITORY_STATE_ROOT` contiene catalogo
SQLite y estado de correo/mercados, temporales de recepcion, planes y recibos,
en volumen local persistente. `IMAGE_REPOSITORY_BACKUP_ROOT` conserva backups
de imagenes y archivos comprimidos en una ubicacion independiente; los backups
de bases de datos/configuracion/metadatos siguen bajo estado local. Sin esas
variables se conserva el layout local anterior. Con NFS se exige fuente exacta
en `IMAGE_REPOSITORY_NFS_SOURCE` y, para backups remotos,
`IMAGE_REPOSITORY_BACKUP_NFS_SOURCE`; ausencia/montaje incorrecto bloquean acceso
sin crear fallback local. Preparacion y limites:
[Synology NFS](../deployment/nfs/README.md).

El catalogo registra EAN, proveedor, origen, vista canonica, mercado, fecha UTC,
SHA-256, huella visual, nombre solicitado y ruta relativa; las asociaciones
de pedidos solo contienen EAN. Las variantes de mercado no suman vistas.
Las tres vistas validas necesitan imagen verificable de al menos 600 px en ambos
ejes y huellas visuales distintas (contrato Kering existente).
Los originales menores tambien se conservan, pero no acreditan completitud.

## Busqueda masiva por EAN

En el menu del repositorio, la opcion administrativa **Busqueda masiva por EAN** permite
pegar codigos o cargar CSV/TXT UTF-8. Solo al pulsar **Analizar listado** se
leen las fuentes configuradas. El limite es 500 entradas y 1 MiB por archivo;
se conserva el codigo literal y se agrupan GTIN equivalentes con ceros iniciales.
Los repetidos cuentan entradas validas adicionales de una misma identidad; los
invalidos se enumeran aparte. XLSX no esta incluido.

Se abre el catalogo existente en modo lectura y se comprueban los originales
con checksum y huella. V1, V2 y V3 exigen imagenes validas y huellas distintas;
detalle y vistas desconocidas no completan cobertura. "Sin registros" solo
describe el catalogo, no una busqueda exhaustiva de archivos en el NAS. Si el
catalogo o el almacenamiento no responde, la cobertura es desconocida. El visor
lee los originales comprobados sin crear miniaturas, ZIP, copias ni bloqueos.

La consulta Odoo usa exclusivamente los codigos indicados, sin filtrar stock,
con transaccion de lectura y limite de resultados. Una relacion en
`product_supplierinfo` acredita la plantilla, no la variante ni su vigencia;
las compras confirmadas son solo evidencia historica. Un vinculo
proveedor–portal confirmado puede producir un candidato, nunca confirmar por si
solo la ruta. Marcas fuera del ambito configurado, proveedores multiples y
variantes ambiguas requieren revision. No hay una fuente configurada de
asociaciones confirmadas de variante–proveedor–portal: hasta definirla y
verificarla no se producen rutas confirmadas en la interfaz. Ningun resultado
autoriza adquisiciones, solicitudes o conexiones con portales.

Bloqueos de fichero compartidos por EAN cubren consulta, descarga y escritura.
SQLite usa transacciones durables; los bytes se escriben antes de publicar
metadatos. Todas las herramientas deben usar la misma raiz y sus bloqueos.
SQLite activo nunca se admite sobre NFS. Los locks de bytes permanecen junto a
las imagenes para coordinar escritores; NFS debe acreditar locking y rename.

### Granularidad Luxoptica comprobada

Kering consulta el EAN bajo bloqueo y no abre el portal si V1/V2/V3 estan completas.
Para un EAN parcial enumera todo el visor, omite URLs ya validas y conserva
vistas adicionales/desconocidas. No deduce vistas del orden ni del nombre
generado. La identificacion previa y su evidencia se describen en
[Kering](KERING_IMAGES.md#deteccion-de-vistas-y-alcance-actual-issue-38).
Los generadores de solicitudes Luxoptica excluyen EAN que ya tienen las tres
vistas. El adaptador del portal Luxoptica existente selecciona **Todas las
vistas** y Graph recibe un ZIP opaco completo: no existe en este repositorio un
API verificado de descarga por vista. Para un EAN parcial se transfiere ese ZIP,
se deduplican sus bytes al incorporarlo y se conservan alternativas distintas.
No afirmar que se evita la transferencia de entradas individuales ya validas
de ese ZIP. Cambiar la seleccion del portal requiere contrastar opciones y
contratos reales; no se inventan selectores ni se descartan alternativas.
Esta excepcion para EAN parciales fue aceptada por el solicitante durante la
implementacion: permitir ZIP completo, deduplicar al importar y documentarlo.

## CLI (ejemplos para staging, no ejecutar en produccion)

No existe migracion automatica al arrancar la app. El inventario usa solo
lectura y escribe su plan fuera de los origenes. Origen y destino deben ser
separados. En Windows usar rutas de Windows; en Cloud usar sus rutas montadas.

```powershell
python migrate_image_repository.py inventory --source "Luxoptica=E:\staging\lux-images" --source "Kering=E:\staging\kering" --target "E:\staging\common" --output "E:\staging\plan.json"
python migrate_image_repository.py apply --plan "E:\staging\plan.json"
python migrate_image_repository.py verify --plan "E:\staging\plan.json"
python migrate_image_repository.py recover --plan "E:\staging\plan.json" --target "E:\staging\recovery"
$env:IMAGE_REPOSITORY_ROOT = "E:\staging\common"
python migrate_image_repository.py sync-nas --target "E:\staging\nas-replica"
python migrate_image_repository.py recover --plan "E:\staging\plan.json" --repository "E:\staging\nas-replica" --target "E:\staging\nas-recovery"
```

`inventory` es tambien la simulacion: muestra destinos propuestos, colisiones,
checksums, alias y bloqueos. `apply` rechaza un plan alterado, originales
cambiados, EAN sin resolver o imagenes no reutilizables. Estas ultimas requieren
revision/reparacion sobre una copia de staging, conservando el backup original,
y repetir el inventario; no hay un bypass silencioso. Para resolver ambiguedades, pasar `--mapping`
al inventario con un JSON revisado, no editar el plan:

```json
{
  "files": {
    "E:\\staging\\legacy\\foto.png": {
      "ean": "0012345678901",
      "view": "frontal",
      "market": "",
      "origin": "integracion_original",
      "orders": ["6094"],
      "metadata": {"modelo": "GG0998S"}
    }
  }
}
```

Los planes nuevos v2 firman tambien las raices independientes de estado y
backups. Los planes v1 siguen validos para su layout original, no para cambiar
la ubicacion mediante variables de entorno; repetir inventario antes del corte.
Los recibos contienen la ruta efectiva si ya existian contenidos en destino.
`.migration/<plan>/originals` conserva **todos** los archivos inventariados,
incluidos JSON, ZIP, informes, configuracion e historial SQLite, sin cambiar
bytes. No exponer esta carpeta por HTTP: puede contener metadatos privados o
configuracion cifrada. La recuperacion reconstruye cada origen bajo su ID
estable (hash de su ruta), comprueba todos los checksums y nunca sobrescribe
originales. En layout legacy una replica incluye catalogo, imagenes, planes y
backups. En layout separado `sync-nas` legacy se bloquea expresamente; verificar
y recuperar usan estado local y backup independiente firmados en el plan.

Validacion offline:

```powershell
python -m pytest tests\test_image_repository.py tests\test_kering_images.py -q
```

Inventario/simulacion **local**, 2026-10-10 (no Cloud): 1.211 archivos,
1.204 imagenes, cuatro snapshots historicos de pedidos. Se detectan 388
imagenes sin EAN univoco y siete que no cumplen la validacion de reutilizacion.
El plan queda bloqueado hasta resolver mapeos y las siete imagenes no
reutilizables; no se aplico la migracion ni se
creo el destino. Estas cantidades no describen el volumen de produccion.

## Plan de migracion (no ejecutado en produccion)

1. Identificar volumen real, rutas externas/NAS, todos los proveedores y tareas.
   Pausar escritores antiguos y nuevos durante inventario y corte; guardar
   backups recuperables de volumen, configuracion e historial. No borrar origen.
2. Crear inventario/simulacion con la CLI. Incluir raiz antigua Luxoptica,
   raiz `KERING_DATA_ROOT` y cualquier otra integracion. Un EAN ambiguo necesita
   un mapeo revisado: no se asigna a un EAN inventado. Revisar archivos sin EAN,
   corrupcion, colisiones, alias, metadatos y pedidos.
3. Revisar el plan JSON. Completar mapeos y repetir el inventario si cambia
   cualquier origen. Primero ensayar con copias en staging.
4. Aplicar **explicitamente** el plan revisado. Copiar, no mover. Cada archivo
   tiene checksum, backup y recibo durable. Una repeticion retoma entradas
   verificadas; cambios en el origen invalidan el plan.
5. Verificar todos los destinos, backups, referencias por EAN y restaurar a
   otro directorio. Comparar bytes/checksums con inventario y comprobar galeria,
   pedidos historicos, ZIP y pendientes. Conservar snapshots/results originales.
6. Configurar la raiz comun en todos los servicios y desplegar el codigo.
   Recuperar estado de correo migrado antes de reactivar Graph. El historial
   Kering sigue en su raiz persistente original. Los pendientes se recalculan
   desde representaciones, no desde rutas antiguas.
7. En el layout legacy, replicar al NAS mediante copia verificada del catalogo
   y archivos. En el layout NFS separado, el catalogo operativo permanece local
   y `sync-nas` legacy esta bloqueado: usar backups independientes y el ensayo
   de recuperacion de `deployment/nfs/README.md`, nunca copiar la exportacion
   de imagenes sobre si misma. Revisar jobs externos para que lean `<raiz>/<EAN>`
   y no rutas por proveedor/modelo.
8. Reactivar procesos y validar en piloto. Rollback: pausar, volver a codigo/
   configuracion anteriores y usar originales intactos o la recuperacion
   verificada. **Esta herramienta nunca elimina originales**. Retirada posterior
   solo con aprobacion humana y politica de retencion tras verificar recuperacion.
