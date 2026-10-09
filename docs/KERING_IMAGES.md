# Imagenes Kering (PR borrador, adaptador real)

## Estado real de la integracion

El 2026-10-09 se inspecciono una sesion autorizada del portal
https://my.keringeyewear.com/keringeyewear/es/login, sin leer ni exportar cookies,
credenciales o datos de cuenta. Solo se capturaron las fotografias del producto.
Se implemento `KeringPortal` y se inyecta desde la UI con configuracion cifrada;
`UnverifiedPortal` queda como fallback de llamadas sin adaptador, no de la pantalla.

Evidencia real:

- Login publico: MAIL, CONTRASENA, Iniciar Sesion; autenticacion indicada por
  enlace `/es/logout`. No se deduce acceso solamente porque desaparezca el login.
- Busqueda observada: `.showSearchBar`, `#hard-js-site-search-input`, Enter,
  formulario `#search_form`. El portal construye la consulta; no se inventan URLs.
- Buscando EAN **8056376560527**, los resultados incluyeron **MB0412S-001**,
  referencia interna 30016317001. La ficha confirma EAN 8056376560527 y UPC
  distinto 889652542942. No se asume conversion entre ambos. Se acepta solo
  coincidencia textual exacta de EAN o UPC en `.characteristics-title` y valor
  hermano; se registra modelo, color y talla de esa misma ficha.
- Originales del visor `#imageModal .itemModal img`: indices 0/1/2 son
  perspectiva/frontal/detalle en la muestra. El cuarto es editorial con modelo,
  no se cuenta. **No hay lateral independiente: se guarda detalle, no lateral.**
  El orden y el detalle se han contrastado tambien en BV1012S-004; conviene
  ampliar muestras si se incorporan familias con convenciones distintas.
- Los tres originales respondieron HTTP 200, image/png, **2400x1286** frente a
  galeria 512x512 y miniaturas 128x128. Bytes: perspectiva 116124, frontal 99353,
  detalle 159631. Huellas de pixels distintas. Se descarga la mayor resolucion
  que el visor publica, sin modificar la URL ni inventar transformaciones.
- Host de originales observado: `picture.kecdn.net`; las URLs opacas se leen
  directamente del DOM, no se generan por patron.

Se ejecuto ademas `scripts/validate_kering_portal.py` con credenciales introducidas
por el administrador en Configuracion y leidas solo desde almacenamiento cifrado:

| EAN | Modelo/color/talla | Acceso | Primera descarga | Repeticion | Repositorio parcial |
| --- | --- | --- | --- | --- | --- |
| 8056376560527 | MB0412S / 001 / M | Confirmado | Frontal, perspectiva, detalle | 0 busquedas, 0 descargas | 1 busqueda, 1 descarga |
| 8056376294347 | BV1012S / 004 / XL | Confirmado | Frontal, perspectiva, detalle | 0 busquedas, 0 descargas | 1 busqueda, 1 descarga |

En ambos casos, la segunda ejecucion reutilizo tres archivos validos. El parcial
se preparo en un repositorio temporal con frontal y perspectiva y recupero
unicamente detalle. Las carpetas de validacion se eliminaron al terminar.
Se corrigio durante la prueba el cierre explicito de conexiones SQLite, necesario
para poder limpiar temporales en Windows. No se imprime config ni secretos.

La PR permanece en borrador para revision. CI y build Docker remotos pasaron
para el codigo validado: run 37903991259, commit 06f6170, el 2026-10-09.
Verify dependency locks, pruebas offline, lint y sintaxis: success;
build-and-push: success (build de PR, sin publicacion de imagen ni despliegue).
Deploy to Server: skipped. No se han fusionado PR ni desplegado cambios.
CAPTCHA/MFA y caducidad
se prueban con simuladores, no con desafios reales. No se ha encontrado todavia
una ficha real con menos de tres originales; el parcial manual es un repositorio
incompleto y no debe presentarse como producto incompleto real de Kering.

## Arquitectura y permisos

- Streamlit: `kering_images_ui.py`, integrado en `app_enhanced.py`.
- Tanto configuracion como pantalla de pedidos, historial y ZIP requieren el rol
  `admin` existente; el rol `masterdata` no tiene acceso.
- Odoo: reutiliza `get_db_engine()` y la configuracion PostgreSQL `DB_*` existente.
  Solo ejecuta SELECT; la transaccion de pedidos se marca READ ONLY y se revierte.
  No modifica pedidos, lineas ni fichas. El usuario DB debe tener solo permisos
  de lectura sobre `purchase_order`, `purchase_order_line`, `product_product` y
  `res_partner`. La conexion SQL no aplica reglas de registro de Odoo: conceder
  acceso DB solamente al conjunto de datos autorizado para esta aplicacion.
- Consulta por `partner_id` exacto por defecto, `date_order`, excluyendo `cancel`.
  Una opcion explicita, desactivada por defecto, incluye contactos con el mismo
  `COALESCE(commercial_partner_id,id)` de la entidad seleccionada. No se comparan
  nombres. Ver contactos asociados permite revisar la relacion real antes de ampliar
  alcance. Contactos archivados pueden tener pedidos historicos validos.
  `order_ids` se filtra en SQL con `bindparam(expanding=True)` y valores enteros,
  sin releer el intervalo entero por pedido. Lista vacia no consulta; datos no
  enteros se rechazan. Requiere columnas Odoo `display_type`,
  `product_id`, `barcode`, `commercial_partner_id` y `supplier_rank`; el esquema real no se ha validado
  contra produccion. Secciones y notas no participan en la comprobacion.
  Se reviso mediante SELECT la relacion comercial del proveedor guardado:
  solo una entrada asociada a su propia entidad, sin contactos adicionales.
  No se exportaron nombres, emails ni datos de cuenta. La ampliacion de alcance
  sigue siendo explicita por si esta relacion cambia en Odoo.
- Fechas: dias completos Europe/Madrid, intervalo UTC semiabierto desde medianoche
  hasta medianoche del dia posterior al final. Incluye ambos dias, incluso en DST.
  Se presupone el almacenamiento estandar Odoo `date_order` UTC sin zona.
  La tabla de seleccion presenta fechas locales de Madrid.

## Configuracion segura

Usuario y contrasena iniciales vacios. URL predeterminada: la indicada arriba.
En Configuracion > Kering, seleccionar proveedor Odoo y guardar los datos.
La contrasena se introduce enmascarada, nunca se rellena con el secreto guardado;
dejarla vacia conserva la anterior. La opcion Eliminar credenciales las borra.
Comprobar configuracion verifica clave, proveedor y campos obligatorios;
**no prueba el login ni la descarga real**.

Probar acceso a Kering es una accion distinta: login real en segundo plano y
comprobacion del marcador autenticado; no descarga productos. Las credenciales
no se toman de constantes ni parametros CLI. Configuraciones cifradas anteriores
sin `include_commercial_contacts` siguen funcionando con la opcion false.
No activar DEBUG/trazas/videos de Playwright ni capturas de login/cuenta.

La configuracion existente de otras integraciones usa `.env` sin cifrado; no se
reutiliza ese almacenamiento para usuario/contrasena Kering. Se guarda el documento
completo en `config.enc` cifrado y autenticado con Fernet. Los errores de interfaz
y de producto son mensajes/codigos fijos; no incluyen excepciones del proveedor.
No se exportan credenciales en ZIP ni en historial.

Configurar en el servidor una clave Fernet aleatoria de 32 bytes codificada URL-safe:

- Preferido: archivo externo al repositorio y al volumen de datos; montar como
  Docker secret y establecer `KERING_ENCRYPTION_KEY_FILE=/run/secrets/kering_key`.
- Alternativa: `KERING_ENCRYPTION_KEY` inyectada por el gestor de secretos del servidor.
- Sin clave valida, la configuracion no se puede guardar/descifrar. No hay clave
  predeterminada, derivada de la contrasena de la app ni guardada en Git.
- No imprimir la clave, contrasenas ni documentos descifrados. Restringir archivos
  de clave a su propietario (0600) y directorio de datos a usuarios autorizados
  (0700). Mantener la clave en un backup separado; perderla impide recuperar
  las credenciales. Para rotar, descifrar con la clave anterior y guardar con la
  nueva en una ventana administrativa antes de retirar la anterior.

Ejemplo de override de Docker Compose (solo rutas, ningun valor secreto):

```yaml
services:
  abcd-app:
    environment:
      KERING_ENCRYPTION_KEY_FILE: /run/secrets/kering_key
    secrets:
      - kering_key
secrets:
  kering_key:
    file: /etc/abcd-secrets/kering.key
```

Provisionar ese archivo con el gestor de secretos. Fernet.generate_key() es la API
para generar una clave nueva; escribirla directamente al archivo protegido,
sin mostrarla ni introducirla en chat. No incluir datos reales en CI.

## Persistencia, procesamiento y galeria

`KERING_DATA_ROOT` es `/app/data/kering` en Docker. En local se usa
`masterdata_data/kering`. El volumen `./masterdata_data:/app/data` existente
permite escrituras incluso con el contenedor read_only y sobrevive a despliegues.
No eliminar ese volumen al desplegar. Copiarlo junto con un backup coherente
de SQLite (usar sqlite3.Connection.backup, no copiar solamente el .sqlite3
durante escrituras WAL). Los datos estan excluidos de Git y del contexto Docker.

- `config.enc`: configuracion cifrada.
- `history.sqlite3`: ejecuciones, intentos, snapshots de lineas, resultados y
  metadatos de archivos; WAL y synchronous FULL.
- `images/kering/<EAN>/<vista>.img`: bytes originales sin transformaciones ni
  imagenes sinteticas; extension interna neutra, se exporta extension real en ZIP.
- `locks/<EAN>.lock`: bloqueo FileLock entre procesos sobre almacenamiento local.
  No desplegar este mecanismo sobre NFS/varios servidores sin validar bloqueos
  y SQLite o sustituirlos por almacenamiento/bloqueo compartido adecuado.

Antes de consultar el adaptador se validan archivo, checksum, decodificacion,
formato JPEG/PNG/WebP, tamano minimo 600x600 y huella RGB normalizada para rechazar
duplicados exactos o cambios simples de resolucion. El umbral es conservador y
no equivale a confirmar la resolucion maxima del portal. La huella no demuestra
semanticamente las vistas; esa prueba depende de la evidencia del adaptador real.
Se admite detalle cuando no hay lateral disponible, sin etiquetado falso.
Un archivo ausente/corrupto vuelve a ser pendiente. Se guardan archivos con
tempfile, fsync y reemplazo atomico, sin reemplazar vistas ya validadas. Los mismos
EAN reutilizan archivos entre pedidos. La galeria existente muestra el mercado
Kering a partir de metadatos y archivos comprobados, no mezclado con Luxottica.

Dos lotes como maximo por proceso, en ThreadPoolExecutor fuera de Streamlit.
Chromium se inicia de forma lazy solo para EAN pendientes y se cierra por lote;
un EAN completo no provoca ni busqueda, ni descarga, ni login.
Un EAN ocupado queda pendiente con `ean_en_proceso`; reintentar cuando termine.
Los EAN repetidos se consultan una vez por lote. El heartbeat mantiene viva una
ejecucion larga. Tras 5 minutos sin heartbeat, al abrir historial o iniciar lote,
los pedidos sin terminar se marcan Interrumpido, conservando vistas ya escritas.
No hay reanudacion automatica tras reiniciar: usar Reintentar pendientes y errores.

Estados del intento: Pendiente, En proceso, Completo, Parcial, Error, Interrumpido.
Completo exige EAN y tres vistas validas para todas las lineas aplicables.
Un pedido sin lineas aplicables es completo de forma vacua. Lineas sin EAN
quedan registradas y evitan completar. Cada intento conserva pedido, proveedor,
fecha, rango, usuario, ejecucion y resultados por EAN/vista. Los intentos anteriores
son inmutables como evidencia; una corrupcion posterior no reescribe el resultado
historico, pero el visor actual la muestra pendiente y el proximo intento la repara.
Se releen pedidos al ejecutar cada intento y al consultar fechas; nunca se omite
un pedido solo por su estado anterior. Un cambio de proveedor, cancelacion o salida
del rango provoca error de lectura del intento, sin procesar datos fuera del filtro.

La pantalla incluye seleccion multiple, progreso automatico cada 3 segundos,
detalle, tres espacios por producto, motivos, ZIP con archivos validos, filtros
de historial por numero, fecha de pedido, fecha de ejecucion y estado, y reintento.
El ZIP de historial corresponde al snapshot de ese intento, no a futuras lineas.

## Adaptador y limites

`fetch(ean,pending)` reproduce la busqueda observada y revisa hasta 40 candidatos
en la pagina de resultados. Solo devuelve originales de una ficha cuyo EAN/UPC
coincida exactamente, cuya referencia modelo-color sea coherente y tenga talla.
Se usan recursos HTTPS de hosts confirmados, sin seguir redirects de imagen.
HTML, miniaturas, duplicados y editoriales no cuentan como tres vistas.
El mapeo de indices del visor requiere contraste adicional si Kering cambia
su layout/orden o publica familias con convenciones distintas.

Timeout de navegacion/HTTP 30 s. Una reautenticacion por fetch si caduca sesion,
conservando bytes obtenidos para no repetir descargas; motor maximo tres intentos
por EAN. Login fallido y producto no encontrado no se reintentan. CAPTCHA/MFA
visible requiere intervencion, sin eludirlo. Deteccion conservadora de campos OTP,
iframes habituales y mensajes de verificacion; desafios distintos pueden acabar
como timeout y requerir revision manual. No se guardan perfiles/cookies.
Errores y repr usan mensajes fijos, sin excepciones crudas ni configuracion.

Validacion manual opt-in (no ejecutar desde CI), credenciales cifradas guardadas:

```bash
python scripts/validate_kering_portal.py --ean 8056376560527 --allow-network
```

Usa carpeta temporal aislada: acceso, descarga completa, segunda ejecucion con
cero busquedas/descargas, repositorio parcial con frontal/perspectiva y reintento
que debe recuperar solo una vista. No imprime credenciales ni las recibe por CLI.
El parcial corresponde a datos locales, no demuestra una ficha real incompleta.
El flujo con credenciales configuradas ya paso para las dos referencias arriba.
Pendientes: ampliar familias y encontrar una ficha realmente incompleta;
build Docker local no disponible. Build/CI remoto ya comprobados en verde.

## Validacion offline

```bash
python -m pytest -q tests/test_kering_images.py
python -m pytest -q tests test_transform_luxottica_masterdata.py test_validate_masterdata_odoo_dryrun.py
python -m flake8 kering_images.py kering_images_ui.py kering_portal.py scripts/dependency_locks.py scripts/validate_kering_portal.py tests/test_kering_images.py --select=E9,F63,F7,F82
python -m py_compile kering_images.py kering_images_ui.py kering_portal.py scripts/dependency_locks.py scripts/validate_kering_portal.py
```

Las pruebas usan SQLite como Odoo simulado, portales FakePortal y fotografias
sinteticas exclusivamente dentro de tests. Navegador y HTTP tambien simulados.
Cubren inyeccion de config, login, caducidad, timeout, intervencion, identidad exacta,
SQL por IDs/contactos, pendientes con detalle existente y errores sin secretos;
fechas/DST, proveedor/cancelados,
notas/secciones, ceros, EAN ausentes/duplicados, completos/parciales, reutilizacion,
archivos ausentes/corruptos, concurrencia, interrupciones, reintentos, cambios,
persistencia, permisos y secreto no pre-rellenado. Un guard impide conexiones
socket externas e inicio de Playwright en estas pruebas.
**Ninguna prueba automatizada valida el portal Kering real**.

Comprobaciones locales (2026-10-09): 30 pruebas Kering y 101 en la suite exacta
del workflow, todas aprobadas; lint estricto y py_compile pasan. Diez avisos de
deprecacion SQLite datetime en Python 3.13. Evidencia real separada arriba.

## Locks y dependencia de PR

El fallo original de Verify dependency locks, run 37106371795, se produjo por
cabeceras/anotaciones --constraint distintas y nueva resolucion sin pins:
MarkupSafe 3.0.3 -> 3.0.4, SQLAlchemy 2.1.2 -> 2.1.3, Streamlit 1.64 -> 1.65 y
WebSockets 16.1.1 -> 17.1. Se conservan las versiones fijadas del commit anterior.
Generacion/verificacion compartida con uv 0.12.22, Python 3.11/Linux x86_64,
constraints de los locks actuales, --no-annotate y cabecera canonica. CI conserva
comparacion de bytes estricta; no se ha eliminado la comprobacion.

```bash
python scripts/dependency_locks.py --write  # regenerar sin actualizar pins
python scripts/dependency_locks.py         # verificar: codigo 1 si difiere
```

PR #36 sobre PR #35 (`copilot/adyen-odoo-reconciliation-pr`), ambas abiertas
el 2026-10-09. #35 depende de `copilot/order-alerts-scheduler`. Orden: integrar
las bases de #35, despues #35, despues #36. Solo cuando #35 se integre en la rama
destino, ajustar base de #36 y repetir CI. No se han fusionado ni desplegado PR.
#36 se mantiene en borrador mientras falte validar el flujo real o falle CI.

Dependencias nuevas: cryptography (Fernet), filelock (bloqueo entre procesos),
tzdata (zonas en Windows). Pillow, SQLAlchemy, Streamlit y Playwright ya existian.
Los locks de produccion/desarrollo se generan con uv para Python 3.11/Linux.
El Dockerfile se ha actualizado para copiar los modulos; no se ha probado un
build Docker local porque no hay motor Docker disponible. Chromium local si esta
instalado y se utilizo en las dos validaciones reales anteriores. El build Docker
remoto de GitHub Actions si paso; no equivale a desplegar ni a probar el portal
real desde dentro del contenedor de produccion.