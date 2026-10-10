# Precopia y cierre coherente: preparacion, no parada autorizada

Continua [CAPTURE.md](CAPTURE.md). No se ha hecho precopia de fotos reales,
parado produccion ni creado snapshots. Este protocolo sustituye la copia
integra durante pausa como primera opcion, SIN rebajar la coherencia.

## Conclusion de la revision

SI se puede anticipar la transferencia de archivos a una **candidata**
privada con servicios activos. No es fuente congelada ni sirve para migrar.
SQLite, WAL y SHM NO se obtienen de esa precopia: se capturan juntos en local
cuando TODOS los escritores Cloud esten detenidos.

**No basta finalizar rsync por tamano/mtime.** Una sustitucion con mismo
tamano/fecha puede pasar inadvertida. Ni dos listados iguales, ni un rsync
exit0 con quick-check, ni checksums calculados despues sobre la candidata
demuestran que correspondia al origen durante pausa. El test sintetico
reproduce esa diferencia y comprueba que `--checksum` la detecta.

Para mover TODO el escaneo de contenido fuera de la indisponibilidad hacen
falta una instantanea recuperable del origen o un registro de cambios
verificado. No se dispone aun de ninguno acreditado. Hay dos rutas:

| Ruta | Durante pausa | Despues de reanudar |
| --- | --- | --- |
| A: preferida, condicionada a snapshot probado | Drenar/parar4servicios, capturar DB locales consistentes, fijar instantanea local inmutable y recibo de punto de captura | Cerrar diferencias contra snapshot (no contra origen vivo), SHA256 completos, inventario, migracion y recovery staging |
| B: disponible sin snapshot, mayor pausa | Drenar/parar4servicios, cierre rsync con checksum sobre TODOS los archivos seleccionados, transferir diferencias, capturar DB locales y asegurar copia durable | SHA256 de la fuente capturada, validacion/inventario/migracion/recovery, sin comparar otra vez con produccion viva |

RutaB solo TRANSFIERE diferencias, pero LEE todos los bytes para comprobarlas.
No se promete pausa corta confundiendo transferencia con verificacion.
Si el requisito es no leer contenido completo durante pausa, rutaB no lo
satisface: ejecutar rutaA solo despues de acreditar el mecanismo de snapshot.
No dar por resuelto ese bloqueo usando mtime, ctime, inode o fechas preservadas.

## Fuente y snapshot: evidencias del servidor

- Origenes Cloud en `/dev/mapper/ubuntu--vg-ubuntu--lv`, ext4, montaje `/`.
  `rsync3.2.7` y `setpriv` disponibles en el servidor.
- `lvs --readonly` como rubensg fallo por permisos de device-mapper.
  No se conocen extents libres ni tipo/espacio COW utilizable, ni hay snapshot
  de Cloud creado/montado y verificado. No se eludio sudo con Docker.
- Antes de rutaA: administrador revisa LV/VG, capacidad COW y crecimiento
  durante toda verificacion, prueba creacion/montaje/lectura/recovery en
  entorno aislado y acredita punto consistente de ext4 y bases. LVM
  por si solo no garantiza consistencia de un FS vivo. El tratamiento
  del journal y cualquier freeze deben probarse; NO `fsfreeze /` ahora ni
  montar con `noload` suponiendo que omitir journal lo hace coherente.
- Snapshot de DSM de staging NO congela las fotos originales locales.
  Fuente local+SQLite local deben pertenecer al mismo intervalo sin escritores.
  Snapshot RO debe quedar disponible hasta validar origen/candidata/backups;
  invalidarlo/llenar COW invalida la captura, no justifica seguir con quick-check.
- Sin esa evidencia no hay comandos LVM listos para produccion ni rutaA
  aprobada. Las instrucciones sudo existentes de NFS no autorizan snapshots.

## Cuatro servicios y efecto sobre usuarios

| Servicio exacto | Efecto de la pausa |
| --- | --- |
| `abcd-control` | Cloud no disponible: inicio, busqueda, galerias, ZIP, configuracion y acciones manuales. Sesiones WebSocket se desconectan; puede requerir recargar al volver. |
| `abcd-luxoptica-monitor` | Se retrasan consulta Graph/correos, importaciones de imagenes y variantes de mercado. No afirmar que hay nuevas fotos mientras esta parado. |
| `abcd-control-abcd-kering-scheduler-1` | Se retrasa la comprobacion/procesamiento programado Kering. No nuevos pedidos/descargas durante pausa; no se altera auto_enabled ni se fuerzan ejecuciones al volver. |
| `abcd-order-alerts` | Se retrasan comprobaciones/avisos automaticos; acciones manuales tampoco disponibles con UI parada. Drenar envios antes, no interrumpir un email y prometer entrega exactamente una vez. |

Odoo y portales externos no se detienen/modifican. Otras apps del servidor
no se paran. No Docker restart general, compose up, cambios de env, owners
de DB activas, ni nueva programacion. Restaurar contenedores existentes
SOLO si estaban running, configuracion/ID/imagen previos, sin disparar jobs
manualmente ni afirmar que arrancar un scheduler equivale a no consultar
proveedores: retomara su comportamiento productivo previamente autorizado.

## Aviso de disponibilidad

Comunicar ventana con antelacion y banner/aviso operativo externo; no inventar
un modo mantenimiento que la app no tiene. El bloqueo de nuevos trabajos es
coordinacion con usuarios, no un flag implementado. Si no se puede controlar
admision y comprobar ausencia de trabajos locales, no iniciar parada.

## Fases y comandos preparados

### 0. Preflight y candidata, servicios activos

Mismo mount/export, cinco roles0700 y controles de capacidad de STAGING
actualizados a186GiB agregados, no la plantilla138GiB anterior.
La candidata y versiones desplazadas van a `work/captures/<RUN>/`; la
fuente final a `source/<RUN>/`. Estado, recibos, DB y SHA256 siguen LOCAL.
No aliases, symlinks, mounts internos, ruta padre o replica sobre si misma.
Validar cada ruta canonical/exact mount con las guardas existentes antes
de crear o copiar; no basta `test -d /mnt/cloud-imagenes`.
No cruzar montajes al recorrer origenes. No seguir symlinks de ficheros.

Verificar bajo1037 lectura real de TODOS los origenes antes de ventana,
sin abrir SQLite ni ampliar permisos; lectura de las5DB ya acreditada.
Aplicar limite de ancho de banda a precopia para no perjudicar usuarios.

Los comandos rsync siguientes son para un operador ya como1037:100 sin
grupos suplementarios (`sudo setpriv --reuid=1037 --regid=100 --clear-groups`
requiere autorizacion sudo, no ejecutada). No se prepara destino como root:

```sh
set -euo pipefail
umask 077
RUN="capture-$(date -u +%Y%m%dT%H%M%SZ)"
BASE="/mnt/cloud-imagenes/staging/work/captures/$RUN"
FINAL="/mnt/cloud-imagenes/staging/source/$RUN"
# Tras preflight1037 sin --create y validacion de aliases/overlaps:
test ! -e "$BASE"
test ! -e "$FINAL"
mkdir -p "$BASE/candidate" "$BASE/retained"
# Los mkdir nuevos NAS deben verificarse1037:100/0700 por ACL.

OPTIONS=(-rt --backup
  --exclude='staging/' --exclude='*.sqlite3'
  --exclude='*.sqlite3-wal' --exclude='*.sqlite3-shm'
  --exclude='*.sqlite3-journal' --exclude='*.lock')
precopy() {
  ORIGINAL="$1"; ROLE="$2"
  case "$ROLE" in Luxoptica|Kering|Common|Luxoptica-manifests|Luxoptica-docs) ;; *) return 1 ;; esac
  PASS="pre-$(date -u +%Y%m%dT%H%M%S)-$ROLE"
  mkdir -m 0700 "$BASE/candidate/$ROLE"
  rsync "${OPTIONS[@]}" --bwlimit=10240 \
    --backup-dir="$BASE/retained/$PASS" \
    "$ORIGINAL/" "$BASE/candidate/$ROLE/"
}
precopy /opt/abcd-control/repo/images Luxoptica
precopy /opt/abcd-control/masterdata_data/kering Kering
precopy /opt/abcd-control/image_repository Common
precopy /opt/abcd-control/luxoptica_data Luxoptica-manifests
precopy /opt/abcd-control/docs/Luxoptica Luxoptica-docs
```

Esta receta no es un ejecutor completo ni invocar con rutas no auditadas.
`BASE` es un directorio NUEVO resuelto exacto bajo work y no origen/Fotos.
La politica chmod0700 de carpetas nuevas de preflight no cambia archivos
productivos. Bloquear si herencia ACL no conserva privacidad. No usar
`--inplace`, `--append`, `--ignore-existing` ni `--size-only`.
ZIP, fotos invalidas/ambiguas, manifiestos y JSON se incluyen sin renombrado
ni inventar EAN. Auditar DB con otras extensiones y sus sidecars ANTES:
las exclusiones no significan que "*.sqlite3" enumere todas las bases.
SQLite se traslada por flujo local, no se descarta.

Una precopia puede advertir archivo cambiado/desaparecido(exit23/24).
Registrar candidata INCOMPLETA/no migrable; nunca ignorar exit distinto0
ni convertirlo en exito. Repetir pasada verificada con directorio/backup
de pasada nuevo o corregir antes de ventana. Si persiste carga/cambios
intensos, reprogramar. No capturar origen incompleto para reducir pausa.

### 1. Cierre autorizado: escoger A o B ANTES de pausar

No autorizacion implicita en este documento. Cerrar admision de nuevos
jobs y esperar drenaje local hasta10min ANTES de iniciar la parada.
Si no drenan o hay envios/descargas activos, no parar: abortar ventana.
Registrar los4ID/imagen/running, salud, config/raiz y timestamps de inicio.
Usar trap/consola independiente de CAPTURE, no activar apagados previamente.
Todos stopped sin ExitCode137 antes de fijar fuente o copiar SQLite.

**RutaA**: despues de obtener5backups SQLite locales cerrados y fijar
snapshot consistente validado, guardar recibo duradero de fuente+DB.
Reanudar INMEDIATAMENTE los4 previamente running; no esperar rsync final,
SHA256, decodificacion, mapeos ni apply/recovery. El rsync final se hace
contra raices del snapshot RO, jamas contra rutas productivas vivas.
Revalidar snapshot y capacidad COW antes/despues de cada pasada.

**RutaB**: no snapshot. Mientras TODOS esten parados, ejecutar cierre:

```sh
# SOLO rutaB dentro de la ventana protegida por trap.
close_tree() {
  ORIGINAL="$1"; ROLE="$2"
  case "$ROLE" in Luxoptica|Kering|Common|Luxoptica-manifests|Luxoptica-docs) ;; *) return 1 ;; esac
  test -d "$BASE/candidate/$ROLE"
  test ! -e "$BASE/retained/close-$ROLE"
  rsync "${OPTIONS[@]}" --checksum --delete-delay \
    --backup-dir="$BASE/retained/close-$ROLE" \
    "$ORIGINAL/" "$BASE/candidate/$ROLE/"
}
close_tree /opt/abcd-control/repo/images Luxoptica
close_tree /opt/abcd-control/masterdata_data/kering Kering
close_tree /opt/abcd-control/image_repository Common
close_tree /opt/abcd-control/luxoptica_data Luxoptica-manifests
close_tree /opt/abcd-control/docs/Luxoptica Luxoptica-docs
# Capturar DB+WAL LOCAL por snapshot_sqlite_copy con RUN igual,
# usando los5comandos de CAPTURE. Ninguna DB/WAL operativo va a NAS.
# Revalidar todos stopped, recibos y ausencia de archivos parciales/journals.
sync -f "$BASE"
test ! -e "$FINAL"
mv "$BASE/candidate" "$FINAL"
sync -f "$FINAL"
# Recibo LOCAL durable "captured-awaiting-validation"; restaurar AHORA.
# No ejecutar inventario/galeria/ZIP/apply/recover dentro del trap de pausa.
```

RutaA usa este cierre tras reanudar, sustituyendo los5ORIGINAL por su
correspondiente ruta RO del snapshot validado; SOLO mientras este conservado.

`--delete-delay` se permite EXCLUSIVAMENTE sobre la candidata nueva, para
que archivos ya ausentes en el punto de captura no contaminen fuente final.
Con `--backup` y backup-dir NUEVO, versiones reemplazadas/eliminadas se
desplazan a retained, sin perder bytes. No es borrado de originales, NAS
productivo ni remoto. Si no se puede acreditar retencion, NO ejecutarlo.
Todos los archivos retirados de candidata siguen inventariados como versiones
previas en work con procedencia, no se migran como fotos actuales ni se borran.
No `--delete-excluded`: DB sidecars/exclusiones se conservan en flujo local,
no limpiar asi una candidata ajena o una precopia de otra configuracion.
El test prueba reemplazo, eliminacion, mismo tamano/fecha, nuevos archivos,
preservacion invalidas/ambiguas y exclusiones. Es sintetico, no captura real.

### 2. Ya restaurados: validacion pesada y ensayo

Captura asegurada NO equivale a migracion aprobada. Guardar recibos de
comandos/exit0, punto de captura, listas y variantes retenidas, backupsDB y
staging inmutable para la app. Estado "captured-awaiting-validation", nunca
"verified" por mtime. Nadie escribe source; solo mounts RO durante validacion.

Calcular SHA256 completos de fuente, comparar contra snapshot si rutaA,
verificar copias y DB locales(integrity/FK/recuentos/historial), firmas y
cobertura Common/proveedor/mercado/EAN. No comparar contra produccion viva
despues del reinicio: ya puede haber archivos nuevos/modificados.
Un mismatch/error invalida captura y BLOQUEA ensayo; no corregir original,
descartar archivos ni inventar EAN. Conservar recibos y copias para diagnostico.
Realizar inventario/simulacion v2, migracion SOLO staging, verify/repeticion/
recuperacion, galeria/ZIP/nombres/asociaciones/checksums, sin prolongar parada.
Los bloqueos de mapeos/invalidas permanecen aunque captura sea consistente.

Para ensayo con fuente porRUN, la plantilla Compose admite los paths del
history NUEVO mediante variables de soporte, sin cambiar contenedores activos:

```sh
export STAGING_HISTORY_SNAPSHOT="/opt/cloud-image-staging/state/frozen/$RUN/Kering/history.sqlite3"
export STAGING_HISTORY_TARGET="/nas/staging/source/$RUN/Kering/history.sqlite3"
```

El RUN debe ser el mismo del recibo capturado; paths absolutos canonicos,
snapshot LOCAL0600 cerrado. Preparar un punto de bind vacio en ese source/Kering,
nunca copiar DB operativo al NAS. Mantener source sin otros writers y RO
en validacion; punto de bind no se cuenta como imagen. Los origenes del
inventario son `source/$RUN/{Luxoptica,Kering,Common,...}`, no work/retained.
La configuracion por defecto anterior sigue disponible para ensayos sinRUN;
no mezclarla con el nuevo history ni usar una DB de otro momento.

## Duracion: indisponibilidad separada de verificacion

Sin throughput medido de18,78GB ni snapshot probado:

- Precopia: sin indisponibilidad, al limite propuesto10MiB/s una pasada de
 18,78GB orienta30min MAS lectura/listados/archivos activos. No es benchmark.
- RutaA: objetivo de parada5-15min (stop/snapshot/DB/recibo/reinicio), solo
 despues de ensayo de snapshot. No comprometida ni habilitada ahora.
- RutaB: reservar20-60min de parada. A20-50MiB/s, leer origen+candidata
 ~37,6GB para cierre orienta12-30min, mas diferencias/SQLite/sync/reinicio.
 Con delta grande o NAS lento puede exceder60min. No prometer "solo diferencias"
 como duracion; calcular presupuesto con medicion y volumen cambiado antes.
- Validacion SHA256/decodificacion/mapeos/inventario (anterior1003s), ensayo
 completo y recovery: FUERA de indisponibilidad; pueden llevar decenas de
 minutos/horas. No usar17min previos como estimacion medida de este ensayo.

## Aborto y restauracion

Antes de parar: fallos permisos/guardas/capacidad, snapshot no probado (rutaA),
precopia persistente no completada, nuevos tiposDB/symlinks sin auditar,
jobs/envios activos tras10min o sin admision cerrada -> no iniciar ventana.

Durante: writer aun running/terminacion137, error de copia/SQLite/retencion,
journal no revisado, NAS inaccesible/montaje cambiado, snapshot invalido/COW
agotado -> marcar INCOMPLETE y restaurar inmediatamente. Limite de captura
5min antes del presupuesto total de pausa:10min rutaA/55min rutaB, reservar
5min para reiniciar y verificar. Si el rendimiento previo no cabe, abortar
ANTES de la pausa, no bajar controles para terminar.

Responsable con consola independiente/watchdog comprueba reloj y restaura
los4previamente running incluso si proceso de copia se bloquea en NFS hard;
una senal/timeout puede NO interrumpir syscallNFS. No esperar checksum/sync
indefinido para restaurar Cloud local. Cancelar solo job de captura identificado
y no promover candidata mientras pueda seguir escribiendo. No desmontar
NFS compartido ni matar por nombre contenedores/procesos del host.
Restauracion docker start del ID previo, running/saludUI+alertas en2min,
identidad/config intactas. Si no healthy, incidente explicito y operador,
no despliegue/composeup/restaurarDB productivas. Conservar toda captura parcial
con estadofallo, no usarla como fuente ni borrar originales/versiones.

## Capacidad y limites

No reducir reserva agregada por usar delta. Candidata~18,78GB,
retained puede crecer con todas las variantes durante precopia dentro de
work16GiB: **16GiB NO admite candidata completa18,78GB**.
Para este protocolo reservar **work64GiB** (candidata24+retained24 y conservar
16GiB para ZIP/extraccion/concurrencia), ademas de source24+images8+backups64+
recovery24+2reserva: **186GiB agregados**,199.715.979.264 B.
Tras mv candidata->source no contar dos veces, pero mantener reserva de
crecimiento y repetir preflight antes de cada fase. Retained sin purga puede
exceder24GiB: abortar/recalcular, nunca borrar versiones para hacer hueco.
Esto no son cuotas ni un backup independiente ante falloNAS.

Estado/SQLite/recibos locales; los5DB+WAL observados254.032B son metadata
previa, no snapshot.3copias+2GiB reserva~2.148.245.744B, frente a~20,25GB
local libres; repetir conteo real. No llevar18,78GB/ZIP/extraccion al local.
Si rutaA necesitaCOW local, requiere capacidad ADICIONAL certificada:
no asignar por defecto esos20GB ni considerarlos extentsLV libres.

## Aviso SQLite de alertas: fallo pendiente, NO esperado

**Actualizacion posterior e37813d:** se corrige `OrderAlertStore` con lock
de inicializacion por ruta canonica compartido entre procesos. Presupuesto
total10s para lock+init, reintentos cortos SOLO para SQLite BUSY/LOCKED;
otros errores se propagan. No reintentar envios ni transacciones de claims.
Todas las conexiones usan busy timeout10s, synchronousFULL y cierre explicito.
Si ya esta enWAL no se intenta volver a cambiarlo. Schema idempotente bajo
lock, lock liberado ante error/muerte del proceso, deduplicacion transaccional
existente conservada. Tests reforzados NO debilitados.
Tests nuevos:6procesos de arranque/envio simultaneos, reinicio sin duplicar,
recuperacion de enviofallido, bloqueoSQLite externo/liberacion/timeout,
lockinit timeout/muerte y corrupcion no silenciada; smoke del artefacto
tambien ejecuta actores independientes con emails sintéticos locales.
El siguiente parrafo conserva el diagnostico HISTORICO anterior al arreglo.

CI38077641374: un hilo murio en `OrderAlertStore.__init__` al ejecutar
`PRAGMA journal_mode=WAL`, `sqlite3.OperationalError: database is locked`.
No era la contencion gestionada de `claim_orders` ni una simulacion esperada.
El test antiguo solo comprobaba un email y no propagaba errores de ambos
hilos: podia pasar si UNO de los actores no llegaba a comprobar pedidos.
No acredita concurrencia satisfactoria aunque CI mostrara300passed.

Se refuerza el test con futures/result para exigir finalizacion de ambos
actores y propagar excepciones/timeouts. Otro test inyecta error de inicializacion
y demuestra que NO se convierte en exito/aviso ignorado. No se preinicializa
DB para esconder carrera ni se silencian warnings. No cambia el runtime
de alertas: la carrera intermitente de inicializacion WAL sigue PENDIENTE.
Una pasada sin error, incluso10repeticiones, NO la corrige ni explica como
esperada. CI roja por este test es un bloqueo real que debe informarse.
Corregirla requiere cambio especifico de inicializacion multiproceso y
regresion determinista, separado de la captura; no tocar bases activas.

## Evidencia de esta revision

- Servicios continuaron running, UI/alertashealthy, mismosStartedAt.
- NAS libre3.041.345.404.928B/local20.253.536.256B (consulta metadata).
- Preflight1037 sin --create con manifiesto NUEVO: reserva199.715.979.264B
 (186GiB), disponible3.041.367.293.952B; mismas5carpetas0700, sin escriturasNAS.
- Acceso LVM sinprivilegios rechazado, snapshot no acreditado.
-19tests alertas reforzados pasaron local y10repeticiones concurrentes sin
 excepciones; resultado intermitente anterior sigue pendiente.
- Protocolo rsync probado con datos sinteticos, no precopia productiva ni
 parada, snapshot, migracion/recoveryreal o replica remota ejecutados.

## Precopia real autorizada: ejecutor acotado preparado

`scripts/precopy_staging.py` no ejecuta stop/start/migracion/snapshot/replica.
Valida exportexacta/roles0700/186GiB, permiso local y fuentesRO sin aliases/
tipos especiales. Lee cabeceras16B para bloquear SQLite con extension no
enumerada, mantiene invalidas/unknown y nombres. Precopia solo inicial nueva,
locklocal compartido no bloqueante, candidate24GiB+versiones/ZIP reserva.
Rsync10MiB/s, sindelete/inplace/ignoreexisting, backup-dir privado porrol;
logs/recibo locales privados, estados precopy-running/candidate-not-migratable
o incomplete. Exit23/24 o permiso invalido es fallo explicitamente retenido.
No firma inventario migrable, no acredita checksums de origen activo.

HerramientaDocker separada con rsync (faltaba en imagenauxiliar), a partir
de imagenlocal auxiliar validada, codigo dePR copiado al build. No publicar,
recrear servicios ni cargar secretos. RUN nuevo e identidad1037:100 con
origenes bindsRO y destinos work/local exclusivamente. Tras resultado:
registrar candidato/no migrable, recuentos/bytes y exclusiones SQLite para
backups posteriores; verificar4servicios/StartedAt iguales. No promocionar
a source ni ejecutar cierreB hasta autorizacion de parada. ViaA/LVM intacta
y pendiente de validacion/autorizacion. Un container de precopia que falla
no justifica borrar candidata/originales: conservarlogs/versiones y revisar.

Preflight real detecto `Kering/config.enc`0:0/0600, no legible1037. No se
amplian permisos activos ni se usa un copiadorprivilegiado para saltarlos.
Config cifrada/keys/.env pertenecen al flujo LOCAL privado de respaldo,
no a NAS/candidataimagenes; excluidos EXPLICITAMENTE, enumerados en recibo
como estadoLOCAL pendiente de captura autorizada. Los originales permanecen
intactos. Captura consistente sigue bloqueada hasta preservar config/clave
localmente con lector autorizado en ventana, sin imprimir secretos ni colocar
claves en logs/Git/NAS. Esta exclusion no equivale a descarte del config.
