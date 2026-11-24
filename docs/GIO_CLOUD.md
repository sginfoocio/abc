# GIO en Cloud: procesamiento offline y puertas de activacion

## Estado de entrega

**No activar en produccion todavia.** Esta entrega implementa y prueba el
procesamiento offline, la cola persistente, la pantalla de administracion y
la preparacion del correo. No acredita una integracion real completa.

| Componente | Evidencia |
|---|---|
| Excel y CSV locales | Leidos programaticamente; originales intactos |
| CSV, decimales, signos, agrupacion y equilibrio | Pruebas sinteticas y muestra real, sin publicar datos personales |
| Portal publico | GET `/login` y JavaScript publico observados; sin autenticar |
| Exportacion autenticada | **Pendiente: no hay credenciales GIO disponibles** |
| Odoo | Esquema e IDs contrastados por SELECT en transaccion READ ONLY y rollback |
| Importacion manual Odoo | **Pendiente: no se ha probado el importador, sus permisos ni la agrupacion real** |
| Correo | Payload, adjuntos, duplicados y estados probados con dobles; **sin envio real de prueba** |
| Produccion | Sin despliegue, reinicio, activacion, merge ni escrituras Odoo |

No se encontro `AGENTS.md` en el repositorio ni en sus directorios ascendientes.
Se han seguido README, workflows, configuracion cifrada de proveedores, permisos
admin, diseno Cloud, SQLite/FileLock y worker existentes.

## Evidencia local y privacidad

Las referencias originales permanecen en la carpeta local `docs/Gio/`.
No estan en el arbol de `origin/main` ni en la rama de esta PR. La inspeccion
de **todas** las referencias locales encontro ademas una instantanea previa
del agente en `refs/agents/.../checkpoints/turn/0` que contiene los cuatro
originales. Esa instantanea no es antecesora de esta rama. No se ha borrado,
reescrito ni publicado; no usar `git push --mirror` ni publicar esas referencias.
La PR solo debe publicar la rama de integracion. Se ignora `docs/Gio/` para
evitar incorporaciones accidentales; Docker ya excluye esa documentacion.

Las fixtures son sinteticas y no incorporan nombres, NIF, encargos, facturas
ni sesiones reales. Los errores conservan fecha/centro, fila del original,
campo, codigo y diferencia, no los datos de cliente. Los originales adjuntos
**si contienen datos personales**: restringir destinatarios y acceso a
administradores. No se modifica ni se repara automaticamente ningun original.

### Excel

`asientos ventas.xlsx`: una hoja (`Hoja1`), 34 filas, 9 columnas; se leyo con
`data_only=False` y `True`. **No contiene formulas**. Los tres ejemplos tienen
las mismas cifras: clientes 1.621,40; IVA 21% 50,65; IVA 10% 120,87; base 21%
241,20; base 10% 1.208,68. Estan equilibrados.

| Tienda indicada por negocio | Centro | Diario por nombre | Cuenta ventas |
|---|---|---|---|
| 0 | Ourense | VENTAS OU | 70000001 |
| 1 | Vigo | VENTAS VI | 70000002 |
| 2 | Coruna | VENTAS CO | 70000005 |

El Excel confirma **nombres, cuentas y orden de las cinco lineas**, no los
IDs numericos de tienda; estos proceden de la especificacion y la captura
asocia Ourense a tienda 0. Clientes usa 43000000; ambos IVA usan 47700000,
pero se mantienen como lineas separadas.

OZ1, VZ1 y CZ1 aparecen como documentos y dentro de conceptos de ejemplo.
No hay formula ni secuencia que permita extrapolarlos. El campo asiento dice
`COGE PROGRAMA`: no se genera numeracion Odoo ni se inventa una factura.
Se propone una referencia descriptiva `GIO fecha centro`, sujeta a aprobacion
contable antes de liberar CSV.

### CSV real

UTF-8 sin BOM, separador `;`, cabecera de 20 columnas. 796 registros,
70 combinaciones fecha/tienda de octubre de 2026:

| Operacion | Filas | Signo observado de TOTAL |
|---|---:|---|
| F.SIMPLIFICADA | 681 | positivo |
| FACTURA | 26 | positivo |
| F.SIMPL.RECTIFICATIVA S1 | 37 | negativo |
| F.SIMPL.RECTIFICATIVA S2 | 50 | negativo |
| RECTIFICATIVA | 2 | negativo |

Se conservan todas las filas y sus signos; no se deduplica por encargo,
cliente, importe ni por igualdad de filas. No se vuelve negativo un importe
por el nombre de la operacion. Las operaciones desconocidas bloquean su grupo.
El tratamiento de las cinco operaciones conocidas es **provisional** hasta
que contabilidad confirme su inclusion y la relacion entre originales,
rectificativas y facturas. No se interpreta `MODO_PAGO` como nueva venta.

El procesador identifica:

- **14 descuadres de TOTAL respecto a los dos totales de IVA.**
- **11 filas con longitud irregular**: ocho con 21 campos, dos con 22 y una
  con 27, frente a los 20 declarados. En la inspeccion aparecen fragmentos
  de pagos en las posiciones adicionales del final. No se recombinan ni
  se supone que el resto de columnas este alineado: se bloquea el grupo
  identificable por fecha/tienda, sin sumar esa fila. Sus totales se etiquetan
  como incompletos.
- Resultado: **51 grupos sin incidencias y 19 bloqueados**. Se generaron
  51 CSV candidatos solo en memoria; no son una certificacion de importacion.

La incidencia pedida es identificable como **fila CSV 3, 2026-10-31, tienda 0**:
145,00 - (116,89 + 11,69) = **16,42 EUR**. TOTAL_10 es 128,58 y el desglose
21% es cero. La captura del portal muestra la misma diferencia. La cabecera
exportada no contiene otra columna monetaria que la explique. Las otras
columnas y el Excel tampoco acreditan una causa. **Sigue sin resolver**:
no se atribuye a descuentos, exentos, anticipos ni errores concretos de GIO.
Pedir al proveedor el detalle y significado de esa operacion y una exportacion
con cabecera y quoting consistentes. El CSV de Ourense/2026-10-31 queda bloqueado;
no se ajusta ni se crea contrapartida por 16,42.

## Portal: observado frente a pendiente

Origen fijo solicitado: `https://opticadiagonal-gioweb.deipe.com/`.
El GET publico redirige a `/login`; se observo GIOweb 1.84.15. El HTML carga
Ext JS desde `/gwcore/production/gioweb_login.js?vg=1.84.15`.
El JavaScript publico define `login_check_url="/login_check"` y los campos
`_username` / `_password`, con IDs `loginInputUsuario` / `loginInputPassword`.
El flujo tambien consulta/registra sesiones web. Esto **no demuestra** el
contrato de cookies, controles adicionales ni la exportacion tras autenticar.
No se enviaron credenciales, no se invocaron acciones de registro de sesion.

Las capturas confirman Consultas -> Listados Oficiales, Detallado Extendido,
Desde/Hasta/Tienda, Buscar y Descargar. Sin acceso autenticado no se han observado
las peticiones de Buscar/Descargar ni selectores estables de esa pantalla.
Por ello `UnverifiedDownloader` falla explicitamente sin conectarse, y la
configuracion y UI rechazan activar el automatico. **No hay endpoints ni
selectores de exportacion inventados**.

Al disponer de credenciales mediante Cloud:

1. Inspeccionar acceso y exportacion con autorizacion, sin guardar cookies,
   passwords, HAR con datos personales ni respuestas en Git/PR/logs.
2. Preferir `requests.Session` si el contrato real lo permite; si requiere
   navegador, reutilizar Playwright y Chromium ya presentes en la imagen.
3. Implementar `fetch(day, centre, config) -> bytes` con filtros exactos
   Desde=Hasta y tienda, timeout y clasificacion explicita de fallos transitorios.
4. Verificar los tres centros, sesion caducada/HTML, dia sin movimientos y
   recuperacion. Mantener el limite de tres intentos y la validacion de alcance.
5. Retirar la puerta de activacion solo junto con esa evidencia y pruebas.

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

### Odoo: IDs reales y formato candidato

Consulta del 17/11/2026: Odoo base **15.0.1.3**, sociedad **2**, moneda EUR.
Los nombres de diario NO son IDs ni sus codigos:

| Centro | ID diario | Codigo | Nombre |
|---|---:|---|---|
| Ourense | 181 | VOU | VENTAS OU |
| Vigo | 182 | VVI | VENTAS VI |
| Coruna | 183 | VCO | VENTAS CO |

IDs de cuentas **de esa sociedad**: 43000000 -> 11138; 47700000 -> 11150;
70000001 -> 11268; 70000002 -> 11269; 70000005 -> 11335.
Existen cuentas con los mismos codigos en otras sociedades: **no resolver
solo por codigo**. No se precargan estos IDs como valores universales.
Los diarios observados no tienen identificadores externos registrados.

La pantalla permite configurar sociedad e IDs tecnicos. Para liberar un
CSV, ademas de aprobaciones documentadas de contabilidad y del importador,
el worker comprueba con SELECT/READ ONLY/rollback que version, moneda,
diarios, cuentas y relaciones de campos siguen coincidiendo. Un fallo conserva
el candidato y registra la incidencia; no recurre a nombres ambiguos.

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
2. Cada centro descarga independientemente. Solo `TransientDownload` admite
   reintento automatico: tres intentos persistidos como maximo, con esperas
   de 60/120 segundos antes de los siguientes ticks. Acceso no verificado
   es error final, distinto de sin movimientos.
3. Se guarda el original exacto bajo SHA-256 antes de validar. La cabecera,
   encoding (UTF-8/CP1252), separador, longitud de filas, fechas, centros,
   operaciones, importes y sumas se validan. En descarga diaria, cualquier
   fecha/centro fuera del solicitado bloquea el archivo, no se filtra en silencio.
   Solo cabecera valida sin filas significa sin movimientos; un archivo vacio
   o HTML no lo significa.
   Una respuesta HTML se conserva privada como evidencia del error de descarga,
   pero no se adjunta al correo como si fuera un informe.
4. Los centros con incidencias no generan CSV. Los restantes generan un candidato
   equilibrado. Solo pasa a `ready` tras aprobaciones y comprobacion SQL real.
   IVA10 e IVA21 nunca se fusionan por usar la misma cuenta.
5. El correo se prepara cuando todos los centros registrados de esa fecha son
   finales. Adjunta todos los originales disponibles y solo los CSV `ready`
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

- Elegir fecha y centros y **Encolar**. Opcionalmente subir un CSV original
  separado por fecha/centro para procesamiento offline. Un CSV mensual completo
  no cumple esos filtros diarios: no se seleccionan filas en silencio.
- La opcion de solicitar correo es explicita y esta sin marcar. Solo se envia
  si, ademas, se han permitido envios reales en configuracion.
- Actualizar el historico para ver estado, incidencias, intentos, originales,
  candidatos/resultados y eventos con actor. Los originales no se muestran
  como tablas con clientes. Cada version se puede descargar como admin.
- **Reintentar centro** reutiliza su original sin repetir descarga; puede
  incorporar un original corregido, conservando el anterior y su huella.
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
   Resolver la descarga autenticada y obtener evidencia por los tres centros;
   no habilitar automatizacion ni envios solo por el login.
2. Acordar operaciones/conceptos y resolver exportaciones irregulares y el
   descuadre de 16,42 con GIO/contabilidad, sin ajustar cifras.
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

Las 61 pruebas GIO sinteticas cubren codificaciones y esquema, coma decimal,
filas repetidas, fecha/centro, signos/abonos, equilibrio y cinco lineas,
16,42, sin movimientos, fallos parciales, HTML de login, limites de reintento,
recuperacion entre etapas, exclusiones mutuas, correo incierto/duplicado,
retirada de aprobaciones, comprobacion SQL readonly, permisos, UI y cambio
de mes/ano/horario Madrid. El conjunto focalizado con correo, navegacion,
Cloud, imagenes y worker existente pasa **263 pruebas**.

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
