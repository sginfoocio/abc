# Synology NFS: PREPARADO, NO ACTIVADO

Objetivo: imágenes en `192.168.1.32:/volume1/cloud-imagenes`, montadas en el
servidor `192.168.1.55` bajo `/mnt/cloud-imagenes`. Sólo bytes de imágenes y locks
compartidos deben vivir en NFS. SQLite, WAL, historial, estado de correo, recibos,
planes y backups de bases de datos deben permanecer en almacenamiento persistente
local o en copias de seguridad independientes, no en un SQLite activo sobre NFS.

## Comprobaciones reales del 10/10/2026

- SSH como rubensg (UID/GID 1000:1000).
- TCP 111 y 2049 del NAS accesibles. **No acredita exportación ni autorización**.
- Actualizacion posterior: `mount.nfs` instalado y montaje real comprobado.
- Sudo no interactivo requiere contraseña; no se usa Docker para eludirlo.
- `/mnt/cloud-imagenes`: origen exacto `192.168.1.32:/volume1/cloud-imagenes`,
  NFS 4.1, rw, hard, TCP, sec=sys, clientaddr=192.168.1.55.
- App, monitor Luxoptica y scheduler Kering ejecutan actualmente como **0:0**.
  No se han cambiado permisos, usuarios, configuración NFS, servicios ni imágenes.
- Disco local: 5.332.529.152 B libres; NAS imagenes: 3.041.245.659.136 B libres.
- Pruebas **reales** de escritura/lectura/fsync/rename/SHA-256/flock entre dos
  procesos correctas como host 1000:1000 y dentro de la imagen actual como 0:0,
  con red del contenedor deshabilitada. Carpetas temporales unicas eliminadas.
  Ninguna imagen existente leida, modificada o migrada por estas pruebas.
- Directorio de prueba propio 1000:1000 con modo 700: escritura correcta.
  La exportacion existente sigue 0:0 y 777; no se ha cambiado su ACL.
- Una creacion temporal desde el contenedor root devuelve propietario 0:0
  y modo 777. No acredita una politica segura de squash ni la ausencia de
  mapeos administrativos: comprobar configuracion Synology con su administrador.
- No se simulo una interrupcion de red/desmontaje sobre el montaje compartido.
  Las pruebas de guard y de migracion en pytest usan datos sinteticos separados.

## Preparación de Synology (administrador del NAS)

Crear/confirmar la exportación indicada y autorizar exclusivamente el host
192.168.1.55, lectura/escritura, seguridad SYS y NFS 4.1.
Conservar root squash; **no mapear root/todos a admin** ni conceder permisos
administrativos. Acordar UID/GID de servicio no privilegiado y otorgar únicamente
acceso a esta carpeta. La opción propuesta es UID/GID 1000:1000, pero debe
verificarse con ACL/identidad real del NAS: no se presupone su equivalencia.
Si root squash rechaza las escrituras actuales 0:0, es un bloqueo válido, no un
motivo para desactivarlo. Cambiar el UID de los contenedores requiere además
preparar permisos de almacenamiento local y el PATH de Python/Playwright
(la imagen actual instala dependencias bajo `/root/.local`).

## Montaje persistente (no ejecutado)

Con sudo autorizado y sin modificar aún Compose ni el repositorio activo:

La unidad visible actualmente es la unidad dinamica del montaje existente:
`FragmentPath` vacio, `SourcePath=/proc/self/mountinfo`, `is-enabled=not-found`.
No es persistente y no hay entrada fstab. Reutilizar su mismo nombre escapado;
no crear una segunda unidad ni un segundo montaje. Plantilla y unidad generada
estan preparadas en `/home/rubensg/cloud-nfs-preparation-20261010`.

Con autorizacion sudo, instalar la unidad sobre ese mismo nombre y habilitarla
para el siguiente arranque, sin desmontar ni reiniciar el montaje actual:

```sh
UNIT="$(systemd-escape --path --suffix=mount /mnt/cloud-imagenes)"
sudo install -m 0644 "/home/rubensg/cloud-nfs-preparation-20261010/$UNIT" "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT"
systemctl show "$UNIT" -p FragmentPath -p SourcePath -p UnitFileState
findmnt -T /mnt/cloud-imagenes -o TARGET,SOURCE,FSTYPE,OPTIONS
```

No ejecutar chmod/chown sobre `/mnt/cloud-imagenes` mientras esta montado:
afectaria al NAS. El eventual modo 000 de la carpeta local subyacente solo se
prepara en una ventana con el montaje ausente y escritores detenidos.
**No basta mientras los contenedores sean root**.
El montaje usa `hard`, TCP y NFS 4.1; nunca `soft`, `nolock` ni un montaje
automático que permita escribir en el directorio local. Se espera la red al
arrancar. Si Synology sólo permite otra versión/export path, revisar y validar;
no degradar silenciosamente.

`scripts/nfs_repository_guard.py --root /mnt/cloud-imagenes` verifica en
mountinfo la fuente exacta, filesystem NFS, montaje raíz, rw y hard.
No crea directorios. Rechaza montaje ausente, local, export distinto, subdirectorio
o soft. El codigo preparado lo integra en el repositorio, consulta/resolucion,
locks, Graph y migracion; comprueba tambien antes de publicar bytes mediante
rename. **No esta desplegado**. Comprobar mountinfo no elimina la carrera entre
la comprobacion y un desmontaje forzado; el bloqueo de activacion incluye
identidad no root, carpeta subyacente protegida y supervision del montaje.

Después del montaje, ejecutar `scripts/test_nfs_image_storage.py` como UID/GID
real del escritor, primero con identidad actual para comprobar squash y después
con la identidad de servicio acordada. Sólo crea una carpeta temporal única
`.cloud-nfs-probe-*`; prueba bytes, fsync, rename, checksum y flock de dos procesos;
limpia exclusivamente sus archivos. No accede a imágenes existentes.
Si falla o se pierde NFS puede quedar esa carpeta de prueba; identificarla
explícitamente antes de limpiarla. No usar borrado recursivo con comodines.

## Arranque y pérdida de conexión: condición previa al corte

La configuración activa Docker tiene `restart: unless-stopped`; un ExecStartPre
externo por sí solo no impide que Docker restaure esos contenedores al arrancar.
Antes del corte deben prepararse y validarse conjuntamente:

1. El codigo preparado implementa `IMAGE_REPOSITORY_STATE_ROOT` local
   (catalogo/WAL/correo/pendientes/planes/recibos) y
   `IMAGE_REPOSITORY_BACKUP_ROOT` independiente (imagenes y archivos ZIP).
   SQLite/historial/configuracion originales se respaldan bajo estado local.
   `KERING_DATA_ROOT`, `PROCESS_ACTIVITY_PATH` y otras bases mantienen volumen
   local. Mantener el comportamiento anterior cuando no se configura separacion.
   No copiar SQLite activo: copia consistente y verificacion antes del corte.
   Un catalogo/estado legacy sin copia separada provoca un bloqueo, no un
   catalogo nuevo vacio ni repeticion silenciosa de correo.
2. Integrar el guard en todos los escritores (Graph, Kering, galería/revisión,
   migración y réplica), antes de mkdir/open/rename, además del arranque.
   No admitir fallback ni éxito ante fallos de NFS. Locks compartidos en NFS;
   locks locales no coordinan clientes distintos.
3. Usar bind con `create_host_path: false`, identidad no root y sin capacidades,
   y dependencia de la unidad mount sólo para los escritores Cloud. No alterar
   el servicio Docker global: también aloja aplicaciones ajenas.
4. Cambiar la política de autoarranque de escritores a una supervisión gated:
   `restart: "no"` y una unidad dedicada con `Requires`/`After`/`BindsTo` del
   montaje y `ExecStartPre` del guard. La política existente no debe mantenerse
   como vía de bypass. Una unidad mount no prueba que el NAS responde.
5. En desconexión, `hard` mantiene las operaciones pendientes, no escribe en
   almacenamiento local. Guard detecta export/montaje incorrecto, no una caída
   de red mientras el kernel conserva el montaje. Healthcheck/watchdog dedicado
   debe declarar servicio no disponible sin inventar éxito. Probar reconexión,
   checksum y recuperación de locks en ventana aislada; no desmontar a la fuerza
   ni usar lazy unmount con escritores activos.

No basta definir variables: todo lo anterior bloquea la activacion. No se ha
instalado la unidad ni cambiado Compose/.env/UID/permisos del NAS. La plantilla
de variables `storage.env.template` es solo de preparacion; no cargarla en
produccion. Falta crear/verificar la exportacion independiente de backups
e indicar su ruta/origen; no se ha inventado una exportacion.

## Simulación de capacidad (basada en inventario previo, no plan ejecutable)

Fuente: inventario privado `20261010-1148-retry` del servidor. Se verifica firma
antes de derivar el cálculo; hay que repetir inventario con escritores pausados
antes de aplicar porque producción ha seguido funcionando.

| Componente | Servidor local | NAS |
| --- | ---: | ---: |
| Originales actuales, ya ocupados | 18.780.210.777 B | 0 |
| Imágenes únicas por EAN/checksum | 0 adicionales | 5.011.781.184 B |
| Backup de SQLite, JSON y demas metadatos | 771.372 B adicionales | 0 |
| Backup de imagenes originales | 0 | 9.893.383.353 B, exportacion independiente |
| Backup de archivos ZIP/archivos comprimidos | 0 | 8.886.056.052 B, exportacion independiente |
| SQLite activo, WAL, historial, planes/recibos | local; medir crecimiento/reserva | 0 |
| Recuperacion de ensayo | 771.372 B metadatos | 18.779.439.405 B en destino independiente, si se ensaya alli |
| Temporal de copia de migracion | 220.332 B | 9.616.571 B imagenes; 4.804.982.367 B backups |
| ZIP bajo demanda | hasta 256 MiB por sesión concurrente | 0 |

El usuario ha elegido una exportacion NAS **independiente** para backups de
imagenes. Incluye originales y ZIP completos, no solo fotografias deduplicadas.
Su origen, montaje y capacidad estan pendientes de provision administrativa.
La copia de archivos se transmite por bloques; el temporal de un ZIP de backup
vive en esa exportacion, no en el disco local. Conserva todos los bytes
inventariados y nunca elimina originales. No contar una replica como gratis.

Los temporales/SQLite son reservas, no una predicción exacta de su concurrencia.
El informe privado derivado registra máximos concretos y separa bytes/GiB.
Resultado del recalculo: SQLite inventariado **110.592 B**, plan privado previo
**4.522.307 B**. Minimo adicional local (metadatos, su temporal, copia de SQLite,
plan previo y una sesion ZIP): **274.060.059 B (0,26 GiB)**. NAS imagenes +
temporal: **5.021.397.755 B (4,68 GiB)**. Exportacion independiente de backups +
temporal: **23.584.421.772 B (21,96 GiB)**; capacidad alli aun no comprobada.
No incluye crecimiento de catalogo/recibos, historiales activos fuera del
inventario, nuevo plan, concurrencia ni snapshots. No es garantia de capacidad.

Graph mantiene `.incoming` local: un ZIP recibido puede requerir otros
**4.804.982.367 B** antes de extraerlo. Sumado al minimo local anterior consume
**5.079.042.426 B**, frente a **5.332.529.152 B** disponibles: solo quedan
253.486.726 B de margen, insuficiente para afirmar operacion segura. Reservar
mas espacio o limitar y medir concurrencia antes de activar; el traslado de
backups no resuelve automaticamente el cache de descargas.
No hubo cambios de tamaño/mtime ni fuentes ausentes respecto al inventario
previo, pero esa comprobación no sustituye nuevos checksums con escrituras pausadas.
Los nuevos inventarios generan planes v2 con raices firmadas de estado/backups;
`apply`, `verify` y `recover` usan esa separacion. Un plan v1 conserva su
comportamiento legacy; si pretende cambiar raices, se rechaza y se exige nuevo
inventario. Esta simulacion derivada del plan anterior NO es un plan ejecutable.

## Réplica y montaje sobre sí mismo

No ejecutar `sync-nas` contra esta misma exportación como origen y destino,
ni mediante otro bind o alias. El control preparado compara rutas, samefile
y mountinfo y rechaza la misma exportacion NFS antes de crear/copiar.
`sync-nas` legacy se bloquea con almacenamiento separado: requiere un mecanismo
de snapshot/recuperacion independiente, no una copia sobre si mismo.
Backup consistente SQLite local mediante API backup;
no copiar WAL activo ni exportar el catálogo activo desde NFS.

Estado final: **preparacion unicamente**, montaje manual real comprobado,
persistencia no instalada, repositorio activo sin cambios, no migracion,
sin permisos ampliados. Solo se escribieron temporales aislados de prueba.

Suite offline completa: **269 correctas**, lint/compilacion correctos; 16 avisos
preexistentes de adaptador datetime SQLite. Pruebas offline del guard:
**9 correctas**, mas regresiones de separacion,
historial, backups ZIP, recuperación y compatibilidad. Cubren montaje
esperado, exportación incorrecta, filesystem local, soft, ro, subdirectorio,
desmontaje/overmount y carpeta ausente sin crear fallback. No son pruebas de
NFS real ni de ACL del Synology.

Smoke adicional del codigo preparado dentro de la imagen Linux actual:
imports, catalogo local, backup de imagen/ZIP separado, repeticion idempotente,
recuperacion y rechazo de montaje ausente correctos. Datos sinteticos en `/tmp`,
red deshabilitada y raiz de contenedor solo lectura; no monta datos productivos.
No sustituye a un build Docker nuevo/CI ni valida reinicio o perdida de red.
App sigue healthy y conserva el bind activo
`/opt/abcd-control/image_repository`. No se publico ni desplego esta rama.

## Continuacion de 9fbf069: comandos y bloqueos administrativos

Identidad definida para servicios de imagenes de staging: **10001:10001**,
usuario `cloud-images` en el artefacto Docker. No existe colision con una cuenta
10001 del host en la consulta realizada. El build coloca Python en `/opt/venv`
y Chromium en `/opt/playwright`, legibles sin acceder a `/root/.local`.
La identidad por defecto de produccion no cambia; el override de staging fija
`user: 10001:10001`, sin capacidades y sin red.

Scripts preparados en `/home/rubensg/cloud-nfs-preparation-20261010`:

```sh
cd /home/rubensg/cloud-nfs-preparation-20261010
sh -n install-persistent-mount.sh
sudo sh ./install-persistent-mount.sh
sudo sh ./set-image-permissions.sh
```

El primero comprueba exportacion/unidad/fstab, verifica la plantilla, instala
la unidad con el mismo nombre dinamico y ejecuta solo daemon-reload/enable.
**No stop/restart/remount/--now**. Si existe ya una definicion persistente,
rechaza reemplazarla. El segundo exige exportacion vacia y actua solo sobre el
directorio raiz: `chown 10001:10001` y `chmod 2770`, nunca `-R`; verifica que
Synology retiene exactamente esos valores. No cambia squash ni asigna admin.
No ejecutar simultaneamente con otra provision/escritura en esa exportacion.

La exportacion se comprobo vacia y sigue 0:0, 777. Sudo no interactivo sigue
requiriendo autenticacion: **los scripts no se han ejecutado**. No se utiliza
Docker para modificar unidades, permisos del host o eludir sudo.

### Propuesta concreta de backups y recuperacion

Provisionar en Synology, con autorizacion limitada a 192.168.1.55 y SYS:

| Uso | Exportacion propuesta (NO existente) | Montaje host |
| --- | --- | --- |
| Backups imagenes/ZIP y staging voluminoso `.work` | `192.168.1.32:/volume1/cloud-migration-backups` | `/mnt/cloud-migration-backups` |
| Imagenes de ensayo | `192.168.1.32:/volume1/cloud-imagenes-staging` | `/mnt/cloud-imagenes-staging` |
| Recuperacion sin solaparse con backup | `192.168.1.32:/volume1/cloud-migration-recovery` | `/mnt/cloud-migration-recovery` |

No sustituir estas raices por subdirectorios del repositorio activo. Verificacion
real `showmount -e` solo publica cloud-imagenes para .55, mas tres exportaciones
de otros clientes; **no se ha probado capacidad ni permisos de las propuestas**.
El volumen montado de imagenes dispone de 3.048.893.317.120 B en la nueva
consulta; puede compartir pool con las propuestas, pero no prueba cuota ni
reserva de esas carpetas. No escribir en exportaciones ajenas.

Reserva propuesta: al menos **64 GiB** para backups + un temporal de copia
(21,96 GiB), ZIP retenidos y 8 GiB de descarga activa + 2 GiB libres protegidos;
al menos **24 GiB** independientes para recuperacion completa y **8 GiB** para
imagenes staging. Medir cuotas/libres con df y statvfs despues de provisionar,
e incluir snapshots/retencion. Backups y temporales comparten capacidad y deben
contabilizarse juntos, no sumarse como espacio libre independiente.

La copia remota `188.227.143.110::Fotos` (`/volume1/Fotos`) mediante rsync se
conserva en la documentacion y no se modifica. TCP 873 y listado rsync desde
.55 exceden el timeout en esta comprobacion: acceso remoto bloqueado.
No se intentaron credenciales ni se escribio en ese NAS. Esa replica excluye
ZIP y no acredita plan, backups de todos los originales, checksums de recibos
ni recuperacion: no equivale a la exportacion de backup de migracion.

### Graph: reserva y concurrencia resueltas en codigo, activacion bloqueada

`IMAGE_REPOSITORY_WORK_ROOT=/app/image-backups/.work` traslada ZIP/adjuntos
voluminosos a la exportacion independiente. SQLite y los JSON de estado siguen
locales. La variable de fuente y la raiz exacta de montaje son obligatorias
en NFS; montaje ausente bloquea antes de crear `.work`.

Los importadores adquieren un lock compartido `.incoming.lock` durante consulta,
descarga y extraccion: solo un importador Graph activo entre servicios usando
esta configuracion. El limite por archivo es 8 GiB; antes de contactar Graph
se requieren 8 GiB + **2 GiB libres protegidos**. Cada bloque comprueba cuota y
capacidad de nuevo; un fallo genera error, limpia su `.part` cuando el montaje
esta verificado y no reemplaza ni elimina archivos anteriores.
Los adjuntos HTTP y ZIP por enlace se transmiten por bloques de 1 MiB; no se
carga el ZIP completo en memoria. `contentBytes` inline ya viene en la respuesta
Graph; se limita antes de decodificar, pero ese canal no puede convertirse en
streaming despues de recibir el JSON. No afirmar memoria acotada a 1 MiB alli.

La extraccion no materializa todo el ZIP en disco local: incorpora una imagen
cada vez al repositorio, con limite **30 MiB por entrada y 50.000 entradas**.
Se conservan nombres/colisiones y ZIP originales; si no se puede resolver EAN,
la descarga queda conservada y se devuelve error. Los ZIP retenidos consumen
espacio real; no hay limpieza automatica destructiva. La reserva se vuelve a
comprobar en la siguiente descarga. Validar retencion/cuotas en NAS antes del corte.

Asi, los 4,80 GB del ZIP grande previo dejan de sumarse a los 274 MB de minimo
local de migracion. Reservar al menos **2 GiB locales** para catalogo/historial,
WAL, planes y exportaciones ZIP bajo demanda (256 MiB por sesion); limitar y
medir sesiones concurrentes en la ventana de staging. No se considera seguro
el escenario anterior de solo 253 MB de margen. Si `.work` no esta provisionado,
el corte permanece bloqueado; no activar un fallback local.

### Ensayo staging, sin produccion

`staging.compose.yml` no publica puertos, no monta pedidos ni credenciales,
no tiene red, no reinicia automaticamente y rechaza binds ausentes. Antes de
usarlo, provisionar las tres exportaciones y el estado local
`/opt/cloud-image-staging/state` con 10001:10001, permisos 0700. Montar solamente
copias anonimizadas en `/staging/source` (solo lectura), un directorio privado
de planes local y el destino independiente `/staging/recovery`.

Con un artefacto cuyo smoke/identidad de build hayan pasado:

```sh
python migrate_image_repository.py inventory --source Other=/staging/source --target /staging/images --output /staging/state/plan.json
python migrate_image_repository.py apply --plan /staging/state/plan.json
python migrate_image_repository.py verify --plan /staging/state/plan.json
python migrate_image_repository.py apply --plan /staging/state/plan.json
python migrate_image_repository.py recover --plan /staging/state/plan.json --target /staging/recovery
```

Estos comandos son **dentro del contenedor staging**, nunca sobre origenes
productivos. Verificar sumas y nombres antes/despues, historiales/asociaciones,
segunda ejecucion idempotente, galeria/ZIP y restauracion. Los datos reales de
Kering del commit 3c807b3 permanecen documentados; vistas desconocidas siguen
pendientes de evidencia, no se declaran acreditadas por descargar mas fotos.

### Evidencia real aislada de arranque, desconexion y permisos

`validate-isolated-nfs.sh` monta la misma exportacion solo en el namespace
de un contenedor nuevo, con red bridge propia y `nosharecache`. No usa red/PID
del host, volumen productivo ni socket Docker dentro del contenedor. Las
capacidades SYS_ADMIN/NET_ADMIN y perfiles relajados pertenecen exclusivamente
a esta herramienta de prueba, no al artefacto de app ni a staging.compose.

Resultados reales:

- Montaje ausente rechazado por el guard dentro del contenedor antes de montar,
  despues de desmontar su montaje aislado y despues de reiniciar el contenedor.
- Con su interfaz propia bajada, la escritura hard-NFS permanece pendiente.
- Tras restaurar interfaz **y ruta por defecto**, termina fsync y el checksum
  coincide. Se corrigio el primer ensayo, que subia la interfaz pero perdia
  la ruta; no era un fallo de recuperacion del montaje compartido.
- Dos escritores con flock se serializan despues de reconectar.
- Solo sus archivos temporales eliminados; exportacion compartida sigue vacia,
  sin stop/unmount/restart del montaje host y app healthy.

**Permisos minimos NO acreditados:** en una carpeta temporal propia 10001:10001
y modo 2770, escritura como UID10001 falla con PermissionError; UID10002 tambien
es rechazado. Propietario/mode POSIX visibles no garantizan ACL/mapeo Synology.
El test de transporte se completo con root, claramente separado del test de
identidad no privilegiada. No mapear UID/root a admin para hacerlo pasar:
administrador NAS debe revisar identidad SYS, ACL de la carpeta compartida,
travesia y permisos efectivos. Repetir el test UID10001 y rechazo UID10002
antes de sustituir 777/activar servicios. No se cambio el 777 de la raiz.

Esto valida reinicio de contenedor sin montaje, no un reinicio del servidor
ni el futuro servicio systemd de escritores. La dependencia BindsTo/Requires
y proteccion de carpeta local subyacente siguen pendientes de instalar y
validar. En una caida de red con hard, mountinfo no detecta falta de respuesta:
no se inventa exito ni se escribe en local; se requiere supervision externa
de disponibilidad antes de activar automatizaciones.
