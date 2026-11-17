# Kering: actualizacion controlada del worker

Fecha de contraste: 17/11/2026, 08:44-08:48 UTC. **Plan preparado, no ejecutado.**
Requiere aprobacion especifica de mantenimiento; fusionar esta documentacion no
autoriza detener, actualizar, procesar pedidos ni acceder al portal.

## Diagnostico

| Componente | SHA observado | Imagen |
|---|---|---|
| main y app `abcd-control` | `c787f5030fa3bab8a45d5b7fd42a51fdda352674` | `ghcr.io/sginfoocio/abc@sha256:40fc72a51cb3a1c4723e60f4209932498d9deb715ce2b3270660932632b0846b` |
| `abcd-control-abcd-kering-scheduler-1` | `2082ee541ab3230d7a87ecd67b0d15b69cf36e66` | `ghcr.io/sginfoocio/abc@sha256:fd00b976d2d39f7d691378bead897897c07b8304f27f0592ef3d04c48905e1f2` |
| Luxottica y alertas | `2082ee5` | Misma imagen antigua; fuera de este cambio |

La app esta healthy; Kering running, sin healthcheck y con cero reinicios.
El nombre del contenedor NO indica su proyecto Compose: sus etiquetas reales son
proyecto `cloud-nas`, servicio `abcd-kering-scheduler`.

El pipeline de [produccion](../../.github/workflows/deploy-cloud-production.yml)
y [controlador](cloud_production_deploy.py) estan disenados para cambiar SOLO
`abcd-app`. `with_image` cambia esa imagen, `compose` apunta al servicio de app y
`ensure_other_services` exige que Kering, Luxottica, alertas y servicios ajenos
permanezcan intactos. Por tanto, desplegar una nueva app no actualiza workers.
No se debe eliminar esa proteccion ni incluir todos los servicios en un `up`
para resolver este desfase.

Los Compose del repositorio comparten `ABCD_IMAGE_REF`, pero no son prueba del
Compose expandido instalado. Dockerfile ya incluye `kering_images.py`,
`kering_jobs.py`, `kering_portal.py`, sus dependencias y Chromium: no falta
desarrollo ni es necesario construir otra imagen exclusiva de Kering.

La app usa `/etc/cloud-production/compose.private.json`. Las etiquetas antiguas
del worker citan `/opt/cloud-image-production/active/compose.private.json`; la
existencia/vigencia de esa ruta no se ha acreditado. **No ejecutarla a ciegas.**
Un administrador debe verificar que el Compose privado aprobado contiene el
servicio Kering actual y su identidad, o preparar uno normalizado compatible.
No se ha leido ni publicado el Compose privado ni sus secretos.

## Diferencias de comportamiento verificadas

- La configuracion observada durante la auditoria tiene automatico activo,
  intervalo 24 h, test mode, pedido permitido 6298 y lote completo sin validar.
  Su estado actual debe releerse antes del mantenimiento; no cambiarlo aqui.
- En el worker antiguo los archivos no contienen los nuevos parametros de
  limites. En main los defaults son tres errores tecnicos y 24 h de espera.
- `automatic_decision` aplica esos limites al historial existente; no limpia
  intentos. Un parcial no tecnico, ausencia de vistas o codigo alternativo exige
  revision, no reintento automatico indefinido.
- Los fallos globales sin intentos tienen limite/revision de lote independiente.
- Procesamiento manual conserva permisos y usa el mismo lock de lote; el
  scheduler respeta ese lock y devuelve espera cuando ya hay una ejecucion.
- Se conservan originales y reutilizacion; detalle no sustituye V2.
- Acceso por fases con presupuesto global 60 s; la deteccion exige host
  permitido, logout presente y buscador visible. Los errores registran fase,
  codigo y duracion; los de preparacion del scheduler registran tipo sin secretos.
- Arrancar el nuevo worker puede escribir recuperacion de runs abandonados y
  estado del scheduler. No es una operacion de solo lectura y necesita permiso.

Pruebas offline sobre main: **293 passed, 2 skipped** en
`test_kering_images`, `test_kering_viewer_order` y `test_cloud_production_deploy`.
Cubren limites, parcial, manual, concurrencia, reutilizacion, fases y proteccion
app-only. No acreditan acceso actual al portal. Los skips locales requieren
consultar el resultado Linux de CI; no son una autorizacion de despliegue.

## Timeout: evidencia y limites

La auditoria encontro 16 intentos historicos del pedido P06290: nueve parciales
y siete `timeout_portal`. Los siete errores apuntan a `redireccion`, entre
17.763 y 37.428 ms. En el ultimo intento no se descargaron archivos y se
reutilizaron cuatro para CH0347S001, con V2 pendiente.

El run del 16/11 09:03 UTC termina Error sin intentos asociados; no hay causa
detallada acreditada para ese run. No confundirlo con los siete timeouts.
No atribuir el fallo a banner, credenciales, MFA, HTML o red sin nueva evidencia,
ni aumentar el timeout por defecto. Mantener #38 abierta.

## Precondiciones y persistencia

Antes de autorizar una ventana:

1. Releer SHA de main, CI, recibo de publicacion y digest inmutable aprobado.
   No usar `latest`. Confirmar que la app/worker son compatibles y que el commit
   incluye las politicas revisadas. Si cambia main, volver a resolver identidad.
2. Autorizar explicitamente desactivar el automatico Kering mediante la UI admin,
   drenar cualquier lote y bloquear nuevas acciones manuales durante la ventana.
   No resetear historial, solicitudes ni `next_run` para forzar una prueba.
   Sin autorizacion para cambiar ese flag, **no iniciar la actualizacion**.
3. Coordinar con el responsable del despliegue app-only para que no haya otro
   despliegue de app/worker durante la transaccion. No ampliar el workflow ni
   modificar su autoridad para incluir workers.
4. Inventariar contenedor, digest/ID local, comando, usuario `1037:100`, red,
   etiquetas, mounts y configuracion expandida del servicio. Guardar la copia
   privada con permisos restringidos; nunca adjuntarla a una PR o recibo publico.
5. Hacer copia consistente/restaurable de SQLite con WAL y del config cifrado,
   y custodiar la misma clave por el mecanismo privado existente. Verificar
   restauracion sobre copia aislada; no copiar solo el archivo sqlite activo.
6. Registrar hashes/recuentos de catalogo, imagenes, runs, intentos y solicitudes,
   separando cambios producidos por otros monitores durante la ventana.
7. Retener y etiquetar localmente la imagen anterior por ID/digest comprobado,
   antes de recrear. Mantener espacio suficiente y no hacer limpieza de imagenes.

Montajes reales de Kering que deben conservarse:

| Host | Contenedor | Uso |
|---|---|---|
| `/mnt/cloud-imagenes` | `/nas` | Originales y NAS |
| `/opt/cloud-image-production/run-20261011T180000Z/masterdata` | `/app/data` | `kering/config.enc`, `kering/history.sqlite3`, datos compartidos |
| `/opt/cloud-image-production/run-20261011T180000Z/image-state` | `/app/image-state` | Catalogo comun y estado |

`KERING_DATA_ROOT=/app/data/kering`, `IMAGE_REPOSITORY_ROOT=/nas/images`.
Se comprobo presencia de clave en entorno sin leer su valor; el campo
`KERING_ENCRYPTION_KEY_FILE` estaba vacio. Conservar el mecanismo y la clave,
no generar otra ni registrar credenciales. Conservar las variables efectivas
de estado/work/backups, no sustituirlas por defaults del Compose de desarrollo.

## Transaccion futura, solo para el administrador autorizado

1. Con automatico desactivado y sin lote activo, aprobar una copia privada
   normalizada del Compose efectivo. Cambiar **solo la imagen del servicio
   `abcd-kering-scheduler`** al digest aprobado. Preservar el container_name
   real `abcd-control-abcd-kering-scheduler-1`, comando, mounts, usuario,
   red y entorno. Cambiar proyecto/nombre podria crear un segundo scheduler.
2. Revisar una comparacion privada antes/despues: cualquier otro cambio bloquea
   la operacion. Si el servicio no existe en el Compose privado, detenerse y
   resolver administrativamente la configuracion; no usar la antigua ruta active.
3. Descargar/verificar el digest aprobado y ejecutar el smoke de dependencias
   sin red externa, usando datos temporales, no los volumenes productivos.
4. Recrear SOLO ese servicio con el Compose aprobado. Ejemplo condicionado a
   que se haya validado el archivo de la app como fuente efectiva de Kering:

   ```sh
   # NO EJECUTADO. Solo tras aprobacion, backup y validacion del Compose privado.
   docker compose --project-name cloud-nas \
     --file /etc/cloud-production/compose.private.json \
     up -d --no-deps --no-build --pull never abcd-kering-scheduler
   ```

5. No ejecutar `down`, `up` sin selector, `--remove-orphans`, limpieza de
   volumenes, reinicio de todos los servicios ni el workflow app-only para
   intentar actualizar Kering.

## Comprobaciones posteriores antes de adquirir nada

- Un unico contenedor para el servicio; SHA/digest nuevo y proceso vivo.
  No hereda un healthcheck HTTP de Streamlit: comprobar scheduler/logs, no
  exigir un endpoint inexistente.
- El automatico sigue desactivado. En al menos dos ciclos de observacion no
  aumenta el numero de intentos ni hay acceso de adquisicion al portal.
- Config cifrado descifrable con la misma clave sin imprimirlo; proveedor,
  test_mode, pedido permitido y corte intactos; nuevos limites efectivos 3/24 h
  salvo otro valor expresamente aprobado.
- Catalogo y SQLite accesibles; imagenes/hash y estados previos preservados.
  Registrar por separado cualquier recuperacion de runs abandonados esperada.
- Ningun otro contenedor cambia de ID, imagen o reinicios; Luxottica conserva
  sus recibos ordinarios y alertas siguen healthy.
- La UI manual permanece disponible y respeta permisos/lock, sin pulsar
  procesamiento real como prueba. Usar pruebas offline para ese comportamiento.

Despues, autorizaciones distintas:

1. **Sonda de acceso**, una vez, sin pedidos ni fotografias:
   `python -B -m scripts.validate_kering_portal --access-only --allow-network`.
   Presupuesto de acceso 60 s, limite externo propuesto 90 s; cerrar navegador,
   conservar fases/codigos y parar ante MFA/CAPTCHA. No ejecutada.
2. **Piloto de un EAN en staging**: separar raiz, estado, SQLite, datos,
   backups, work y miniaturas; no usar `--order-id` ni `--ean` con el entorno
   productivo. Confirmar identidad y vistas disponibles; reimportacion
   idempotente. V2 ausente queda parcial/revision, no inventada.
3. **Reactivacion automatica**, solo tras aceptar las dos pruebas anteriores.
   Revisar antes el limite ya agotado por los intentos historicos; no borrar
   esos intentos para permitir otro lote.

## Rollback

Disparadores: revision/digest incorrectos, segundo scheduler, fallo de
config/clave/mounts, excepcion de inicio, adquisicion inesperada, corrupcion o
regresion de reutilizacion/lock.

1. Mantener automatico desactivado y bloquear procesamiento manual del lote.
   Drenar/detener solo Kering por el procedimiento aprobado.
2. Restaurar **solo** el digest/ID y configuracion del servicio Kering de la
   copia previa, preservando cambios legitimos de otros servicios.
3. Recrear solo Kering con `--no-deps` y validar identidad/mounts.
4. La version anterior no tiene limites nuevos: **no reactivar el automatico
   como parte del rollback**. Mantenerlo deshabilitado hasta nueva decision.
5. No restaurar en caliente bases compartidas ni sobreescribir el NAS. Si hay
   incompatibilidad de esquema/datos, detenerse y revisar una copia; una
   restauracion de datos requiere aprobacion distinta y aislamiento de escritores.
6. Verificar app, Luxottica y alertas intactos y conservar el recibo de fallo.

No hay migracion destructiva prevista. La compatibilidad de esquema se prueba
primero sobre copias. Este documento no amplia privilegios del runner ni cambia
la autoridad root o la politica de aprobacion.
