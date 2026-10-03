# Imagenes Kering (borrador, portal autenticado no verificado)

## Estado real de la integracion

El 2026-10-02 se abrio el portal publico
https://my.keringeyewear.com/keringeyewear/es/login en un navegador.
Se observaron los campos MAIL y CONTRASENA y el boton Iniciar Sesion.
Algunos recursos secundarios presentaban errores de certificado/404.
No se dispuso de una sesion autenticada: no se verificaron la busqueda por EAN,
la correspondencia EAN-referencia-color-talla, los recursos de imagen, las vistas
ni la maxima resolucion. No hay endpoints, URLs de fotografias ni selectores
de producto inventados en esta implementacion.

**La descarga real NO esta implementada ni verificada. No activar en produccion
como descargador operativo.** `UnverifiedPortal` no accede a la red y comunica
`portal_no_verificado`. La pantalla permite consultar pedidos, registrar intentos
y reutilizar archivos previamente validados; no asegura que existan fotografias
reales de Kering. Los nombres frontal/lateral/perspectiva son un contrato propuesto
para el adaptador, NO vistas confirmadas del portal.

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
- Consulta por `partner_id` exacto (no incluye automaticamente proveedores hijos),
  `date_order`, excluyendo `cancel`. Requiere columnas Odoo `display_type`,
  `product_id`, `barcode` y `supplier_rank`; el esquema real no se ha validado
  contra produccion. Secciones y notas no participan en la comprobacion.
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
semanticamente frontal/lateral/perspectiva; esa prueba depende del adaptador real.
Un archivo ausente/corrupto vuelve a ser pendiente. Se guardan archivos con
tempfile, fsync y reemplazo atomico, sin reemplazar vistas ya validadas. Los mismos
EAN reutilizan archivos entre pedidos. La galeria existente muestra el mercado
Kering a partir de metadatos y archivos comprobados, no mezclado con Luxottica.

Dos lotes como maximo por proceso, en ThreadPoolExecutor fuera de Streamlit.
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

## Trabajo pendiente para habilitar Kering real

1. Un administrador debe abrir una sesion autorizada e inspeccionar busqueda y
   descarga, sin enviar credenciales por chat ni registrarlas en capturas/logs.
2. Verificar busqueda de EAN completos (ceros incluidos) y correspondencia exacta
   a referencia, modelo, color y talla; documentar evidencia sin datos sensibles.
3. Confirmar vistas ofrecidas y recursos de maxima resolucion. No usar miniaturas,
   HTML de login ni variaciones del mismo archivo como tres vistas.
4. Implementar un adaptador `fetch(ean, pending)` que devuelva bytes por vista
   validada, solo vistas pendientes. No requiere una nueva infraestructura: el
   Dockerfile ya instala Playwright y Chromium con dependencias del sistema.
5. El adaptador debe usar exclusivamente el acceso autenticado permitido, imponer
   timeout de navegador/HTTP (p. ej. 30 s), gestionar expiracion/reautenticacion y
   comprobar la identidad del producto antes de devolver bytes. El motor admite
   hasta tres intentos por EAN y `InterventionRequired` para CAPTCHA/MFA, pero
   **deteccion real, timeout y reautenticacion aun no estan implementados**.
6. Inyectar el adaptador verificado en BatchService y habilitar la interfaz solo
   despues de validar una muestra real completa/parcial. No eludir CAPTCHA/MFA.

## Validacion offline

```bash
python -m pytest -q tests/test_kering_images.py
python -m pytest -q tests
python -m flake8 kering_images.py kering_images_ui.py tests/test_kering_images.py --select=E9,F63,F7,F82
```

Las pruebas usan SQLite como Odoo simulado, portales FakePortal y fotografias
sinteticas exclusivamente dentro de tests. Cubren fechas/DST, proveedor/cancelados,
notas/secciones, ceros, EAN ausentes/duplicados, completos/parciales, reutilizacion,
archivos ausentes/corruptos, concurrencia, interrupciones, reintentos, cambios,
persistencia, permisos y secreto no pre-rellenado. Un guard impide conexiones
socket externas en estas pruebas. **Ninguna prueba valida el portal Kering real**.

Dependencias nuevas: cryptography (Fernet), filelock (bloqueo entre procesos),
tzdata (zonas en Windows). Pillow, SQLAlchemy, Streamlit y Playwright ya existian.
Los locks de produccion/desarrollo se generan con uv para Python 3.11/Linux.
El Dockerfile se ha actualizado para copiar los modulos; no se ha probado un
build Docker local si no hay motor Docker disponible.