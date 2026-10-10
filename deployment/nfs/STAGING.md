# DSM: lista unica vigente para el ensayo completo

Continuacion de739f77c. Esta lista sustituye las propuestas anteriores de
fuente local y temporales `.work` dentro de backups. **Preparacion solamente**.
NAS192.168.1.32, cliente Cloud192.168.1.55. No tocar cloud-imagenes productivo.

## Carpetas compartidas que crear en DSM

| Uso | Carpeta/exportacion DSM | Montaje en Cloud | Reserva minima libre |
| --- | --- | --- | --- |
| Fuente congelada completa (imagenes, archivos comprimidos, JSON/manifiestos) | `/volume1/cloud-migration-source` | `/mnt/cloud-migration-source` | 24 GiB |
| Imagenes por EAN del ensayo | `/volume1/cloud-imagenes-staging` | `/mnt/cloud-imagenes-staging` | 8 GiB |
| Backups conservados del ensayo | `/volume1/cloud-migration-backups` | `/mnt/cloud-migration-backups` | 64 GiB |
| Recuperacion independiente | `/volume1/cloud-migration-recovery` | `/mnt/cloud-migration-recovery` | 24 GiB |
| ZIP recibidos, streaming y temporales voluminosos | `/volume1/cloud-image-work` | `/mnt/cloud-image-work` | 16 GiB |

No son subcarpetas del repositorio productivo. Crear cinco carpetas compartidas
independientes, sin otros datos. El archivo `staging-exports.tsv` contiene esta
misma lista y las reservas en bytes; instalador/preflight lo utilizan.

Aplicar **la misma regla NFS a las cinco**:

- Host/IP autorizado: **192.168.1.55**, no `*` ni toda la subred.
- Privilegio: lectura/escritura. Seguridad **SYS**, NFS4.1/TCP.
- Squash: **sin asignacion (No mapping)**; conservar UID1037/GID100.
  No mapear root/usuarios a admin; no conceder pertenencia a administrators.
- No activar escritura asincrona. No permitir puertos no privilegiados ni
  acceso a subcarpetas montadas sin una necesidad verificada.
- ACL DSM: usuario **cloud (1037:100)** con lectura/escritura/listado/travesia,
  users/everyone; GID100 no concede acceso. No usar un deny de users que anule
  el grant individual cloud. Mantener acceso administrativo propio del NAS.
- Solo para estas carpetas nuevas/vacias, preparar propietario1037:100 y
  modo0700 compatible con la ACL DSM. Nunca `chmod/chown -R` de carpetas ajenas,
  ni alterar la raiz productiva777 para preparar este ensayo.

Las reservas suman **136 GiB**. Si comparten pool, verificar ese libre agregado
mas snapshots/retencion/crecimiento y las cuotas individuales: cinco `df` sobre
el mismo pool NO prueban cinco reservas independientes. 64 GiB de backups
queda conservado por margen/retencion; los temporales ya tienen sus propios
16 GiB. Work requiere >=10 GiB libres antes de recibir un ZIP de8 GiB con
reserva2 GiB; la cuota16 GiB no sustituye el control por descarga.
Recalcular desde el nuevo inventario si las fuentes crecieron.

## Comandos exactos de preparacion en Cloud

Se entregan en `/home/rubensg/cloud-nfs-preparation-20261010/staging-five-exports`.
Ejecutar **despues de crear las cinco exportaciones/ACL en DSM**:

```sh
cd /home/rubensg/cloud-nfs-preparation-20261010/staging-five-exports
showmount -e 192.168.1.32
sh -n install-staging-mounts.sh
sudo sh ./install-staging-mounts.sh
# Crear solo estado NUEVO. Rechaza estado preexistente para no alterarlo.
sudo sh ./prepare-local-state.sh
# El directorio privado rubensg700 no es atravesable por1037.
# Instalar SOLO herramientas sin secretos fuera de ese directorio.
sudo test ! -e /opt/cloud-image-staging/tools
sudo install -d -m 0755 /opt/cloud-image-staging/tools /opt/cloud-image-staging/tools/scripts
sudo install -m 0644 runtime-code/repository_storage.py staging-exports.tsv \
  /opt/cloud-image-staging/tools/
sudo install -m 0644 runtime-code/scripts/nfs_repository_guard.py \
  runtime-code/scripts/verify_staging_exports.py /opt/cloud-image-staging/tools/scripts/
sudo -u '#1037' -g '#100' -- sh -c \
  'cd /opt/cloud-image-staging/tools && python3 -m scripts.verify_staging_exports --manifest staging-exports.tsv'
```

El instalador genera las cinco unidades con `systemd-escape`, valida todas antes
de instalar, rechaza fstab/definiciones persistentes conflictivas, conserva
montajes ya presentes correctos y solo inicia los destinos staging ausentes.
Opciones exactas:

```text
vers=4.1,proto=tcp,hard,timeo=600,retrans=2,sec=sys,nosuid,nodev,noexec
```

El directorio local SUBYACENTE nuevo queda root0:0/mode000 antes de montar,
para no permitir escritura1037 en fallback. No aplica chmod al directorio NAS.
No stop/restart/remount del montaje compartido ni de Docker. Si falla un nuevo
montaje, aborta y deja explicito el error; no crea fallback ni sustituye origen.
Las unidades se habilitan para arranque. Persistencia del montaje productivo
se gestiona por su instalador existente, por separado; no la modifica este.

Auditoria posterior sin escribir:

```sh
while read -r SHARE ROOT MINIMUM; do
  case "$SHARE" in ''|\#*) continue ;; esac
  findmnt -M "$ROOT" -o TARGET,SOURCE,FSTYPE,OPTIONS
  df -B1 "$ROOT"
  stat -c '%n %u:%g %a' "$ROOT"
  UNIT="$(systemd-escape --path --suffix=mount "$ROOT")"
  systemctl show "$UNIT" -p FragmentPath -p SourcePath -p UnitFileState
done < staging-exports.tsv
```

El preflight comprueba exportacion exacta/no symlink, rw/hard, capacidad por
destino, acceso efectivo1037 y estado local1037:100/0700 con reserva2 GiB.
**No acredita** escritura/fsync/rename/checksum/flock, cuotas agregadas ni ACL
de otros usuarios. Repetir esas pruebas en directorios temporales propios de
CADA exportacion y control1038:100; no usar archivos preexistentes. El ensayo
seguira bloqueado si alguna falla, incluso con mount activo.

Con `STAGING_IMAGE` fijado a un artefacto realmente disponible/verificado,
la herramienta aislada preparada permite probar las cinco exportaciones:

```sh
cd /home/rubensg/cloud-nfs-preparation-20261010/staging-five-exports
: "${STAGING_IMAGE:?Indicar artefacto verificado disponible}"
docker build --build-arg BASE_IMAGE="$STAGING_IMAGE" \
  -f isolation/Dockerfile.probe -t cloud-nfs-probe:staging isolation
while read -r SHARE ROOT MINIMUM; do
  case "$SHARE" in ''|\#*) continue ;; esac
  sh ./validate-isolated-nfs.sh cloud-nfs-probe:staging \
    "192.168.1.32:/volume1/$SHARE" || exit 1
done < staging-exports.tsv
```

La herramienta usa montaje/red propios `nosharecache`, con temporales unicos,
write/read/fsync/rename/checksum1037, control1038, dos flock writers y
perdida/reconexion/reinicio de SU contenedor. No desconecta montajes del host.
SYS_ADMIN/NET_ADMIN pertenecen solo al probe, nunca a servicios de imagenes.
Si un escritor hard sigue pendiente, conservar contenedor/temporales para
recuperacion; no forzar eliminacion ni presentar resultado correcto.

## Fuente completa y SQLite: coordinacion necesaria

No se obtendra una fuente consistente copiando imagenes durante descargas,
ni copiando solo DB mientras WAL cambia. Tampoco se autoriza aqui detener
contenedores activos. Antes de capturar, solicitar **ventana de mantenimiento
explicita** para coordinar app, Luxoptica, Kering y alertas; o provisionar un
snapshot de almacenamiento coherente aprobado con iguales garantias.
Sin esa coordinacion, captura/ensayo completo bloqueados.

En la ventana aprobada, registrar estado previo y todos los escritores, impedir
nuevos trabajos y esperar a los activos; quiescer solo los servicios acordados.
Conservar la configuracion de automatizaciones, no activarlas al restaurar.
No modificar pedidos Odoo ni usar credenciales en logs/capturas.

Preparar estas correspondencias del conjunto completo, sin borrar originales:

- `repo/images` legacy -> fuente `Luxoptica`.
- `masterdata_data/kering` -> fuente `Kering` (imagenes/JSON/config cifrada).
- Repositorio comun actual -> fuente `Common`, incluidos sus metadatos de
  representaciones, pedidos y procedencias; no perder lo nuevo desde el inventario.
- `luxoptica_data` -> `Luxoptica-manifests`, conservando todos los manifiestos.
- SQLite de esas raices y estado compartido -> **copias locales**
  `/opt/cloud-image-staging/state/frozen/`, nunca SQLite operativo en NFS.
  Incluir history, catalogo, process_activity, order_alerts y auth_state si existe.

Crear destinos NUEVOS y privados en esa ventana. Hacer copia de ficheros
sin renombrar, filtrando SQLite/WAL/SHM hacia el flujo local siguiente; no
excluir ZIP/archivos invalidos/EAN ambiguos del inventario para hacerlo pasar.
No usar `rsync --delete`, no sobrescribir copias congeladas previas. Guardar
manifiesto privado de nombres/tamanos/checksums y verificar origen contra copia.
Conservar configuracion cifrada donde sea necesaria, nunca claves/tokens en Git.
Antes de exponer galeria capturas, anonimizar pedidos de manera consistente
sin cambiar EAN/nombres del conjunto privado ni bytes de imagen.

SQLite: con escritores coordinados, copiar DB y WAL presente a scratch
**local nuevo** sin abrir originales. Abrir esa copia, aplicar
`sqlite3.Connection.backup` a snapshot local nuevo, cerrar y comprobar
`PRAGMA integrity_check` y `foreign_key_check`; conservar checksum del fichero
cerrado y recuentos/tablas por base. No copiar SHM, hacer checkpoint del
original, chown de DB activa ni interpretar varias copias activas como un
snapshot global atomico. Si WAL cambia o aparece journal activo, abortar
captura y conservar diagnostico, no forzar importacion.

La copia history final va en
`/opt/cloud-image-staging/state/frozen/Kering/history.sqlite3`,1037:100/0600;
Compose la monta read-only sobre `/staging/source/Kering/history.sqlite3`.
En NAS preparar un punto de montaje VACIO con ese nombre dentro de Kering,
no una DB. Es una excepcion declarada al conjunto de bytes NAS, respaldada
por la copia local verificada; el manifiesto debe distinguirla.
Las otras copias SQLite se conservan locales y deben incorporarse a la
verificacion de estado/restauracion, no suponer que `apply` las importa.

Al acabar captura y comparar todos los checksums, cerrar/sellar la fuente
(sin jobs escribiendo), restaurar exactamente los servicios autorizados
al estado previo. El contenedor de ensayo monta la fuente **solo lectura**.
No ampliar permisos para que una base root-owned activa funcione como1037.

## Ejecucion despues de todas las puertas

Usar artefacto verificado construido desde esta PR (no el codigo productivo
actual), sin red/credenciales/puertos; ejecutar con Compose staging preparado.
`STAGING_IMAGE` debe ser un tag/digest realmente disponible y verificado en
el servidor, **no** un identificador ficticio ni el tag de un build CI no publicado.
Antes de arrancar, confirmar los puntos de montaje fuente/local history y
guardas **dentro del contenedor** para images, backups y work. Para recovery,
usar `require_nfs(Path('/staging/recovery'),
'192.168.1.32:/volume1/cloud-migration-recovery')` antes de copiar/restaurar.
No convertir un fallo de montaje en exito por existir el directorio.

El inventario nuevo debe usar proveedores Luxoptica y Kering separados,
las copias locales SQLite necesarias, todos los manifiestos y un mapping
revisado. Resolver enlaces/rutas de metadata Common sin perder nombres,
procedencias ni asociaciones; si la herramienta no incorpora alguna relacion
legacy, es un **bloqueo**, no un archivo descartable.

Secuencia: inventario/simulacion v2 -> revisar bloqueos/espacio ->
apply -> verify -> segundo apply -> interrupcion/reanudacion controladas ->
recover a destino independiente. Probar tambien reconstruccion/restauracion
LOCAL de catalogo/historial: recover copia originales, no crea por si solo
el estado operativo. No ejecutar un plan v1 retargeteado.

Comparar TODAS las entradas/originales recuperados por checksum y nombre,
fuente intacta, extensiones originales, colisiones sin perdida, EAN/pedidos,
historial snapshots/results, procedencias/mercados/vistas, galeria y ZIP
completo con manifiesto. Exportacion desconocida valida no acredita vista.
Publicar solo cifras/alias y separar prueba real del smoke sintetico.

## Bloqueos vigentes, no omitidos

- Comprobacion SSH de esta continuacion: cinco exportaciones/destinos ausentes.
  Sudo requiere autenticacion; DSM/provision no ejecutados por el asistente.
- Capacidad local20.261.662.720 B (consulta2026-10-10): no usarla para guardar
  fuente completa18.780.210.777 B previa sin reservas; el nuevo tamano se medira.
- ACL productiva confirmada por el responsable no acredita ACL nuevas.
  Montajes/cuotas/permisos/probes de los cinco destinos pendientes.
- Ventana/snapshot coherente de todos los escritores no autorizados aun.
- Local anterior:388 EAN pendientes/7 invalidas. Productivo previo:
  154 claves ambiguas de manifiestos excluidas,0 imagenes sin EAN/invalidas,
  2.187 vistas desconocidas. No son resultados nuevos; no mezclar conjuntos.
- Kering mantiene **Parcial - clasificacion pendiente**. No inferir vistas
  por posicion/cantidad/nombre generado; no inventar significado noshad/shad.
- No migracion productiva, originales eliminados, contenedores activos
  reconfigurados, permisos activos modificados, fusion ni despliegue.

## Validacion de la preparacion entregada

Suite local:289 correctas,2 tests Compose omitidos por ausencia de Docker local,
16 avisos SQLite preexistentes. Los10 casos nuevos cubren cinco destinos,
capacidad al byte, identidad/grupos, ausencia de montaje sin cambios de estado
y seleccion de exportacion en el probe. Lint de nuevos archivos y seleccion
estandar de CI para scripts existentes, compilacion correctos.

En el servidor: parser Compose nativo correcto sin iniciar servicios,
`sh -n` de los scripts y `systemd-analyze verify` de las cinco unidades
generadas correctos. Se eliminaron solo temporales propios de validacion.
Carpeta de herramientas rubensg1000:1000/0700; ninguna unidad instalada ni
exportacion montada/escrita por esta entrega. **No son pruebas NFS de los
destinos nuevos ni un ensayo de migracion real.**
