# Synology NFS: PREPARADO, NO ACTIVADO

Objetivo: imágenes en `192.168.1.32:/volume1/cloud-imagenes`, montadas en el
servidor `192.168.1.55` bajo `/mnt/cloud-imagenes`. Sólo bytes de imágenes y locks
compartidos deben vivir en NFS. SQLite, WAL, historial, estado de correo, recibos,
planes y backups de bases de datos deben permanecer en almacenamiento persistente
local o en copias de seguridad independientes, no en un SQLite activo sobre NFS.

## Comprobaciones reales del 10/10/2026

- SSH como rubensg (UID/GID 1000:1000).
- TCP 111 y 2049 del NAS accesibles. **No acredita exportación ni autorización**.
- `mount.nfs`, `showmount` y `rpcinfo` no disponibles. `nfs-common` no instalado.
- Sudo no interactivo requiere contraseña; no se usa Docker para eludirlo.
- No existe montaje NFS en el servidor ni `/mnt/cloud-imagenes`.
- App, monitor Luxoptica y scheduler Kering ejecutan actualmente como **0:0**.
  No se han cambiado permisos, usuarios, configuración NFS, servicios ni imágenes.
- Disco local: aproximadamente 5 GB libres. Capacidad NAS **no comprobada**.
- Pruebas reales de creación/lectura/rename/checksum/locks **bloqueadas** hasta
  disponer del cliente y del montaje. Los tests del guard son simulados, no una
  validación funcional NFS.

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

```sh
sudo apt-get install nfs-common
showmount -e 192.168.1.32
sudo mkdir -p /mnt/cloud-imagenes
sudo chmod 000 /mnt/cloud-imagenes
UNIT="$(systemd-escape --path --suffix=mount /mnt/cloud-imagenes)"
sudo install -m 0644 cloud-images.mount.template "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT"
findmnt -T /mnt/cloud-imagenes -o TARGET,SOURCE,FSTYPE,OPTIONS
```

El modo 000 protege la carpeta subyacente para escritores no root cuando NFS
no está montado. **No basta mientras los contenedores sean root**.
El montaje usa `hard`, TCP y NFS 4.1; nunca `soft`, `nolock` ni un montaje
automático que permita escribir en el directorio local. Se espera la red al
arrancar. Si Synology sólo permite otra versión/export path, revisar y validar;
no degradar silenciosamente.

`scripts/nfs_repository_guard.py --root /mnt/cloud-imagenes` verifica en
mountinfo la fuente exacta, filesystem NFS, montaje raíz, rw y hard.
No crea directorios. Rechaza montaje ausente, local, export distinto, subdirectorio
o soft. Está **preparado pero aún no integrado en cada escritor**.

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

1. Separar `IMAGE_REPOSITORY_STATE_ROOT` local (catálogo/WAL/estado/recibos) de
   `IMAGE_REPOSITORY_ROOT` NFS. **El código actual no implementa esa variable**:
   guarda `.catalog.sqlite3` y `.migration` bajo la raíz de imágenes.
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

No basta definir variables: todo lo anterior bloquea la activación. No se ha
instalado la unidad ni cambiado Compose/.env/UID/permisos del NAS.

## Simulación de capacidad (basada en inventario previo, no plan ejecutable)

Fuente: inventario privado `20261010-1148-retry` del servidor. Se verifica firma
antes de derivar el cálculo; hay que repetir inventario con escritores pausados
antes de aplicar porque producción ha seguido funcionando.

| Componente | Servidor local | NAS |
| --- | ---: | ---: |
| Originales actuales, ya ocupados | 18.780.210.777 B | 0 |
| Imágenes únicas por EAN/checksum | 0 adicionales | 5.011.781.184 B |
| Backup completo actual (conservador) | 18.780.210.777 B adicionales | 0 |
| SQLite activo, WAL, historial, planes/recibos | local; medir crecimiento/reserva | 0 |
| Recuperación de ensayo | hasta 18.780.210.777 B adicionales si local | 0 |
| Temporal de copia | tamaño máximo de archivo, medir del inventario | máximo de imagen individual, medir del inventario |
| ZIP bajo demanda | hasta 256 MiB por sesión concurrente | 0 |

El tamaño bruto de imágenes del inventario es 9.893.383.353 B; deduplicación
estimada a 5.011.781.184 B. Los backups son una **copia adicional**, no los
originales ya ocupados. Con ~5 GB libres, incluso trasladando imágenes a NAS
no cabe el backup completo local. Requiere ampliar disco o aprobar un destino
independiente de backups; no borrar originales ni depositar SQLite activo en NAS.
La recuperación en otro almacenamiento reduce la necesidad local pero debe
aprobarse y medirse. No se ha contado una réplica adicional como gratis.

Los temporales/SQLite son reservas, no una predicción exacta de su concurrencia.
El informe privado derivado registra máximos concretos y separa bytes/GiB.
Resultado del recálculo: temporal máximo de origen **4.804.982.367 B** (un archivo
grande, no una imagen), máximo de imagen **9.616.571 B**, SQLite inventariado
**110.592 B**. Con backup completo local, su copia temporal, una copia de SQLite
y una sesión ZIP, mínimo adicional local **23.853.739.192 B (22,22 GiB)**;
NAS imágenes + un temporal **5.021.397.755 B (4,68 GiB)**. Falta reserva para
crecimiento, concurrencia y snapshots del Synology. Ensayo de recuperación
local añade hasta **18.780.210.777 B**. Espacio local medido **5.336.969.216 B**.
No hubo cambios de tamaño/mtime ni fuentes ausentes respecto al inventario
previo, pero esa comprobación no sustituye nuevos checksums con escrituras pausadas.
Si se escoge backup externo, recalcular; no cambiar `apply` para omitir backups.
El algoritmo de migración actual ubica backups en la misma raíz, por lo que
**no puede aplicarse tal cual a NFS** con estos requisitos.

## Réplica y montaje sobre sí mismo

No ejecutar `sync-nas` contra esta misma exportación como origen y destino,
ni mediante otro bind o alias. El control actual compara rutas resueltas, no
identidad remota: debe añadirse comparación mountinfo de servidor/export +
subruta y comprobación de identidad de archivos. Rechazar misma exportación
antes de mkdir/copiar. Backup consistente SQLite local mediante API backup;
no copiar WAL activo ni exportar el catálogo activo desde NFS.

Estado final: **preparación únicamente**, no montaje instalado, repositorio
activo sin cambios, no migración ni copia a NAS, sin permisos ampliados.

Pruebas offline: **9 correctas**, lint y compilación correctos. Cubren montaje
esperado, exportación incorrecta, filesystem local, soft, ro, subdirectorio,
desmontaje/overmount y carpeta ausente sin crear fallback. No son pruebas de
NFS real ni de ACL del Synology.
