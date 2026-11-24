# GIO en Cloud: descarga diaria y puertas de activacion

## Estado de entrega

**No fusionar ni desplegar todavia.** La descarga diaria real se verifico en
una validacion anterior; no se repitio sobre el SHA `6891105`. En ese checkout
se consumio localmente el reintento de Vigo desde el original general cacheado,
se genero un CSV candidato y se preparo la vista previa local. El mapeo y el
esquema Odoo se verificaron despues en solo lectura. Siguen pendientes la
validacion del importador en una copia autorizada, la resolucion de los
descuadres y la autorizacion del correo de prueba.

| Componente | Evidencia |
|---|---|
| Portal GIO | GET autenticado devolvio el informe general, coincidente byte a byte con la exportacion manual local; validado antes de `6891105`, no repetido en ese SHA |
| Procesamiento | Tick local exclusivo de GIO consumio el reintento desde el original cacheado; no repitio la descarga ni genero duplicados |
| Interfaz local | Configuracion e historico revisados con sesion local autenticada; no se reprodujo la perdida de sesion. Ourense muestra descuadre, Vigo CSV candidato y Coruña sin movimientos |
| CSV para Odoo | Candidato de Vigo equilibrado, generado con el mapeo cifrado; se reviso la estructura del Excel original como referencia. Mapeo y esquema se verificaron en lectura; el importador no se probo |
| Odoo y correo | Verificacion Odoo en transaccion de solo lectura y rollback; sin escrituras/importaciones. Vista previa local sin destinatarios; no se envio correo. Automatizacion y envios siguen desactivados |
| Produccion | Sin despliegue, reinicio, activacion, merge ni cambios remotos |

No se encontro el worker compartido `kering_jobs.py` en ejecucion. Para no
ejecutar tareas Kering ni de otros proveedores, se uso un tick local exclusivo
de GIO, con el mismo `GIO_DATA_ROOT` y `KERING_ENCRYPTION_KEY_FILE` que establecio
el lanzador aislado de Cloud. Se reutilizo el archivo Fernet existente sin leer,
mostrar ni regenerar su contenido; el ejecutor desactivo red, correo y conexion
Odoo.

Se han seguido las instrucciones `AGENTS.md`, README, workflows, configuracion
cifrada de proveedores, permisos admin, diseno Cloud, SQLite/FileLock y worker
existentes.

## Evidencia local y privacidad

Los originales reales y la evidencia se conservan en almacenamiento local
restringido, no se incorporan a esta rama ni se publican. No se deben incluir
copias reales en Git, documentación, logs ni fixtures.

Las fixtures son sinteticas y no incorporan nombres, NIF, encargos, facturas
ni sesiones reales. Los errores conservan fecha/centro, fila del original,
campo, codigo y diferencia, no los datos de cliente. Los originales adjuntos
**si contienen datos personales**: restringir destinatarios y acceso a
administradores. No se modifica ni se repara automaticamente ningun original.

### Contrato contable

El mapeo de sociedad, cuentas y diarios se configura localmente en Cloud y se
conserva cifrado. Se verifico el mapeo y el esquema con la conexion Cloud
existente, usando una transaccion `READ ONLY`, timeout y rollback. No se
guardan IDs Odoo, importaciones ni cifras de ventas en esta documentacion. Los
CSV son candidatos para `account.move`; esa verificacion no ejercito el
importador ni certifica que acepte el formato.

### Muestra real

La muestra se procesa sin deduplicar filas ni inferir importes a partir del
tipo de operacion. Las filas mal formadas y los descuadres bloquean solo el
centro afectado; no se corrigen ni se generan asientos vacios. Los resultados
detallados se conservan unicamente en el almacenamiento local restringido.

## Portal: descarga autenticada

En local se observó el flujo de **Consultas -> Listados Oficiales -> Detallado
Extendido**, con Desde/Hasta iguales y Tienda vacía. La UI ejecutó la búsqueda
real y el endpoint autenticado de exportación devolvió HTTP 200 con
`application/csv`. Se implementó el mismo GET HTTP observado, reutilizando el
`login_check` ya comprobado; no se llama a `/sesionesweb/registrar`.

El worker ejecutó el informe general de una fecha una sola vez. El original
inmutable se conserva una vez por SHA-256 en el almacenamiento local; las tres
filas de centro apuntan al mismo artefacto general en el histórico. Una segunda
ejecución no volvió a descargar ni duplicó registros. El CSV automático coincidió
byte a byte con la exportación manual facilitada por el usuario.

Resultado separado por centro en el historico local: Ourense, **descuadre
contable** con dos operaciones identificadas y diferencias no nulas, sin
ajustes artificiales; Vigo, **CSV candidato equilibrado**, no liberado para
importar; Coruña, **sin movimientos**, sin asiento vacío. El candidato conserva
las bases imponibles y cuotas del original. Se verificaron cabecera, fecha,
diario, cuentas contra el mapeo cifrado y sumas. El Excel original se reviso
como referencia de estructura contable (fecha, concepto, documento, cuenta,
descripcion, debe y haber); no se hizo una comparacion transaccional de sus
importes con el candidato.

La vista previa local incluye el original general y el CSV candidato, resume las
incidencias de Ourense y no invoca el remitente. No hay destinatarios configurados
en esta instancia. El estado del correo sigue en borrador; no se envio nada.
No se incluyen aquí filas, clientes, importes, NIF, credenciales ni huellas del
archivo original.

Las credenciales se leyeron del almacen cifrado local. Despues del tick se
verificaron la sociedad, los diarios, las cuentas y el esquema Odoo mediante la
conexion Cloud existente en una transaccion de solo lectura, con timeout y
rollback; no se probaron importaciones. No se hicieron escrituras en Odoo,
envios de correo ni registros/cierres de sesion del portal. La automatizacion y
los envios siguieron desactivados. Sin validar el importador ni completar las
aprobaciones, el worker conserva el centro valido como candidato y no publica
un CSV listo para importar.

## Configuracion y arquitectura Cloud

La configuracion de GIO esta integrada en **Configuracion -> GIO**; la seccion
**Contabilidad -> GIO** queda solo para operaciones. Ambas requieren admin.
La pagina operativa no accede al portal ni a Odoo al navegar. Utiliza
componentes y tema Cloud existentes.

- Persistencia: `GIO_DATA_ROOT`, por defecto `masterdata_data/gio`; en Compose,
  `/app/data/gio` dentro del volumen persistente existente.
- Configuracion: `config.enc`, cifrada con el mismo mecanismo Fernet de
  proveedores: `KERING_ENCRYPTION_KEY_FILE` o `KERING_ENCRYPTION_KEY` existente.
  No se genera otra clave ni se escribe un secreto en el repositorio. La clave
  debe ser identica en app/worker; no rotarla sin migrar los cifrados existentes.
- Credenciales GIO: guardar desde **Configuracion -> GIO**; el portal, usuario
  y contraseña se cifran juntos en `config.enc`. La contraseña no se muestra
  al recargar; dejar el campo vacio conserva la guardada y hay eliminacion
  explicita.
- **Probar conexion** realiza GET al portal y POST al `/login_check` observado,
  con los campos publicados `_username`, `_password` e idioma. Solo acepta una
  respuesta JSON de autenticacion exitosa. No sigue redirects del POST ni llama
  a `sesionesweb/registrar`, y cierra la sesion HTTP al terminar. No valida las
  pantallas/exportaciones autenticadas, el importador Odoo ni el correo. Guarda
  estado y hora de comprobacion en el mismo `config.enc`; cambiar URL o
  credenciales invalida ese resultado.
- Hora configurable HH:MM en **Europe/Madrid**, destinatarios y permiso de correo.
  Los controles de automatizacion y correo permanecen bloqueados en esta fase,
  incluso tras una comprobacion de acceso correcta. Los envios reales parten
  **desactivados**; no basta con guardar destinatarios. Una solicitud de correo
  queda pendiente mientras los envios esten deshabilitados.
- Programacion: **worker `kering_jobs.py` existente**, no n8n, cron nuevo,
  servicio nuevo ni hilo ligado a Streamlit. Se incorpora un tick GIO antes del
  tick Kering; un fallo GIO se registra de forma saneada y no impide Kering.
  La configuracion del correo Graph se comparte tambien con ese worker en Compose.
- La UI solo escribe solicitudes duraderas. El worker procesa etapas fuera
  de peticiones HTTP. Debe estar actualizado y en ejecucion para consumirlas.

Una vez autorizado el adaptador, el tick encola el dia natural anterior
cuando la hora local alcanza la configurada. La clave unica por fecha/centro
impide duplicarlo al repetirse una hora de invierno o varios ticks. Si la hora
configurada no existe en el cambio a verano, ejecuta en el primer tick posterior.
Tras una parada de varios dias **no rellena dias antiguos automaticamente**:
recuperarlos explicitamente desde Cloud.

### Odoo: mapeo configurable y formato candidato

La sociedad, los diarios y las cuentas se configuran localmente desde Cloud;
no se incluyen IDs de registros Odoo en esta documentacion. Antes de liberar
un CSV, el worker requiere aprobaciones y comprueba con SELECT/READ ONLY/rollback
que el mapeo y el esquema siguen coincidiendo. Un fallo conserva el candidato
sin recurrir a nombres ambiguos.

Formato candidato: UTF-8 BOM, `;`, decimales con punto, cabeceras tecnicas:

```text
id;date;journal_id/.id;move_type;ref;line_ids/account_id/.id;line_ids/name;line_ids/debit;line_ids/credit
```

Destino propuesto: importador de **account.move / Asientos**, con
`move_type=entry`, no importador de apuntes sueltos. Primera fila: identificador
externo determinista `gio_YYYYMMDD_centro`, fecha, ID de diario, tipo y referencia.
Las cuatro continuaciones dejan vacios esos campos padre y contienen solo
datos de linea. Cinco lineas: clientes, IVA21, IVA10, ventas21, ventas10.
Los abonos invierten debe/haber sin importes negativos en esas columnas.
No se establece `name`, numero de asiento, estado publicado ni factura.

**El esquema SQL no certifica el importador.** Falta validar en una copia de
Odoo con los permisos/configuracion reales que `/.id`, campos y continuaciones
se reconocen, se agrupan en un solo asiento y los ceros/signos son aceptados.
Tambien hay que confirmar el diario de tipo venta para `entry` y los conceptos.
El alcance de esta entrega no autoriza crear, publicar ni escribir asientos
en Odoo. Toda prueba que escriba requiere autorizacion separada en una copia.
No consignar evidencia de aprobacion en Cloud hasta disponer de esa validacion.
Un identificador externo repetido puede actualizar un asiento al reimportarlo:
**no importar dos veces ni usar la numeracion para evitar duplicados**.

## Funcionamiento diario, reintentos y recuperacion

1. La cola usa SQLite WAL local en almacenamiento persistente, con PK
   `(fecha, centro)` y lock de proceso compartido entre worker y acciones
   mutantes. Una seleccion repetida no vuelve a descargar ni enviar.
2. Se descarga un informe general por fecha. Solo `TransientDownload` admite
   reintento automatico: tres intentos persistidos como maximo, con esperas
   de 60/120 segundos antes de los siguientes ticks. Acceso no verificado
   es error final, distinto de sin movimientos.
3. El original general exacto se guarda una vez bajo SHA-256 antes de validar.
   La cabecera, encoding (UTF-8/CP1252), separador, longitud de filas, fechas,
   centros, operaciones, importes y sumas se validan. Fechas fuera del dia
   solicitado bloquean el archivo; las tiendas del informe general se separan
   internamente y no se descarta ninguna fila en silencio. Solo una cabecera
   valida sin filas para un centro significa sin movimientos; un archivo vacio
   o HTML no lo significa.
   Una respuesta HTML se rechaza y nunca se adjunta como si fuera un informe.
4. Los centros con incidencias no generan CSV. Los restantes generan un candidato
   equilibrado. Solo pasa a `ready` tras aprobaciones y comprobacion SQL real.
   IVA10 e IVA21 nunca se fusionan por usar la misma cuenta.
5. El correo se prepara cuando todos los centros registrados de esa fecha son
   finales. Adjunta el original general una sola vez, los originales corregidos
   disponibles y solo los CSV `ready`
   cuyo contrato no haya cambiado. Incluye fecha, centro, estado, filas,
   bases/cuotas/totales e incidencias identificables. Totales de filas ilegibles
   se marcan incompletos; se indican centros no solicitados.
   **COMPLETO** exige los tres centros `ready` o sin movimientos; cualquier
   error, candidato, omision o aprobacion retirada produce **PARCIAL / REVISION**.
6. Reutiliza `send_alert_email` y buzon/remitente M365 existente; no tiene otro
   SMTP. Se persiste `sending`, generacion y manifiesto (destinatarios y huellas)
   antes de la llamada, sin reintentar el POST. Aceptacion del servicio no implica
   entrega. Timeout/error o reinicio durante envio -> `uncertain`, sin reenvio
   automatico. No se intenta prometer exactly-once ante una respuesta perdida.

### Uso manual

- Elegir fecha y centros y **Encolar**. Si no se suben originales separados por
  centro, el worker descarga una vez el informe general para esa fecha. Un CSV
  mensual completo no cumple esos filtros diarios: no se seleccionan filas en
  silencio.
- La opcion de solicitar correo es explicita y esta sin marcar. Solo se envia
  si, ademas, se han permitido envios reales en configuracion.
- Actualizar el historico para ver estado, incidencias, intentos, originales,
  candidatos/resultados y eventos con actor. Los originales no se muestran
  como tablas con clientes. Cada version se puede descargar como admin.
- La vista previa muestra destinatarios, asunto, estados e incidencias, y
  permite descargar el original general y candidatos compatibles para revision.
  Es local y no llama al remitente; el boton no crea una generacion de envio.
  Los descuadres se muestran por fila y tipo de operacion, sin datos de cliente.
- **Reintentar centro** reutiliza el original general sin repetir descarga;
  puede incorporar un original corregido, conservando ambas versiones y sus huellas.
  Regenera solo ese centro con el contrato actual. **No reenvia el correo**.
  No usarlo para ignorar un descuadre: hay que resolver los datos de origen.
- **Solicitar envio/reenvio** crea una nueva generacion registrada para esa
  fecha y todos sus centros ya registrados. Si hubo aceptacion o resultado
  incierto exige confirmar que se ha revisado Elementos enviados y el riesgo
  de duplicado. Revisar tambien destinatarios y aprobaciones vigentes.
- Si un adjunto falta/no coincide con su SHA, se bloquea el correo. Si el total
  base64 excede el presupuesto conservador de 2,5 MB, tambien se bloquea antes
  de enviarlo: descargar desde Cloud o aprobar un mecanismo de adjuntos grandes.
  No se omiten adjuntos en silencio ni se envian solo algunos como si fuera completo.

### Persistencia y mantenimiento

Directorio GIO nuevo con modo 0700, archivos atomicos privados, contenido
inmutable direccionado por SHA; se verifica la huella al leer. Estados,
artefactos/versiones y eventos viven en el volumen local Cloud, no en
almacenamiento temporal, imagen Docker ni un nuevo recurso publico.
App y worker necesitan el mismo montaje, UID/permisos y clave de cifrado.
No compartir el directorio con usuarios no autorizados.

Se conserva historial y originales sin purga automatica: **falta que el
responsable confirme el plazo de retencion contable y de datos personales**.
No se inventa un plazo ni se borra informacion financiera para recuperar
errores. Incluir GIO en el backup/restauracion privado del volumen; copiar
SQLite mediante el procedimiento de snapshot del proyecto, no solo el archivo
principal mientras haya WAL activo. Restaurar juntos DB, archivos y clave
con ambos consumidores parados mediante mantenimiento autorizado.
Los blobs que queden huerfanos por un reinicio entre escritura y commit son
inofensivos y no se purgan automaticamente.

### Activacion (fuera de esta PR)

1. La comprobacion de acceso de Configuracion solo prueba las credenciales.
   La descarga general ya se ha validado localmente; no habilitar automatizacion
   ni envios solo por el login o esa descarga.
2. Acordar operaciones/conceptos y resolver las incidencias y descuadres con
   GIO/contabilidad, sin ajustar cifras.
3. Validar el importador en una copia autorizada de Odoo y registrar evidencia.
4. Preparar directorio persistente protegido, backup/retencion y secretos.
   Aplicar cambios de app **y worker** en mantenimiento autorizado. El
   despliegue Cloud normal es app-only: no actualiza el worker implicitamente.
   Seguir `deployment/cloud/KERING_WORKER_RELEASE.md` para su identidad,
   drenaje y rollback, incluyendo ahora directorio/clave GIO y variables M365.
   El Compose privado real debe reflejar montajes, `GIO_DATA_ROOT`, clave y
   Graph del worker; cambiar el Compose del repositorio no lo despliega.
5. Autorizar un destinatario de prueba y un unico envio explicito. Comprobar
   adjuntos, aceptacion y entrega, sin importar nada en Odoo.
6. Revisar toda la cola pendiente antes de permitir envios. Configurar hora,
   destinatarios y automatico solo despues de superar todas las puertas.

Rollback de codigo: detener consumo GIO y envios con mantenimiento autorizado,
conservar directorio, clave, originales y estados. No borrar la DB para
recuperar la automatizacion: perderia la proteccion de duplicados.

## Pruebas y controles

Las 85 pruebas GIO cubren codificaciones y esquema, coma decimal, filas
repetidas, fecha/centro, signos/abonos, equilibrio, descuadres, descarga general,
centro sin movimientos, fallos parciales, limites de reintento, recuperacion,
correo incierto/duplicado, comprobacion SQL readonly, permisos y UI. La prueba
real contra el portal se registra por separado y no se sustituye por mocks.

Comando general exigido por CI (sin servicios de produccion):

```text
python -m pytest -q tests test_transform_luxottica_masterdata.py test_validate_masterdata_odoo_dryrun.py
python -m scripts.dependency_locks
```

CI tambien ejecuta lint, sintaxis, benchmark sintetico de miniaturas y build
Docker con smoke imports sin red. Se han incluido los modulos GIO en el COPY
explicito y en el smoke de la imagen. Los resultados completos de CI se
deben comprobar sobre el SHA exacto de la PR; no equivalen a descarga
autenticada, importacion Odoo ni envio real.
