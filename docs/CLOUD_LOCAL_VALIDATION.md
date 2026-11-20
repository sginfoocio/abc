# Validación local de Cloud

Esta guía aplica la [política obligatoria del repositorio](../AGENTS.md#política-obligatoria-de-validación-local-previa-a-integración-y-despliegue).

## Preparación

1. Identifica rama, SHA, versión de Python, dependencias y configuración local. No reutilices estados persistentes como destino de escritura de la prueba.
2. Comprueba permisos efectivos, montajes, DNS, VPN, certificados, límites y acceso autorizado. Usa los mecanismos existentes para credenciales sin imprimir contraseñas, tokens ni cookies. Para catálogos y estados productivos, usa acceso de solo lectura; no reconstruyas índices ni generes miniaturas persistentes.
3. Define la muestra, la ventana temporal y las operaciones permitidas antes de conectar. Para cualquier efecto externo, registra la autorización específica, los registros afectados y los límites.

## Infraestructura del piloto de proveedores

Inventario de solo lectura comprobado el 20/11/2026 en el servidor interno `.55`, por SSH con verificación de la clave del host. Repite el inventario antes de cada piloto; el Compose versionado no acredita el runtime.

| Componente | Evidencia y alcance |
|---|---|
| Cloud | `abcd-control` publica TCP 8501. El monitor Luxottica, el scheduler Kering y las alertas no publican puertos. Los cuatro servicios usan UID/GID `1037:100` y la red `cloud-nas_abcd-network`, un bridge no interno. No es necesario añadir el piloto a esa red para comprobar el tramo del host al PostgreSQL. |
| Odoo | No se observó un contenedor Odoo/PostgreSQL, ni siquiera detenido, ni un listener local en TCP 5432. Cloud recibe la configuración PostgreSQL por `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` y `DB_PASSWORD`. El destino configurado resuelve a otra dirección privada y es alcanzable desde `.55`; esto identifica el backend que usa Cloud, no el alojamiento del servidor web de Odoo. |
| Ruta desde Windows | El SSH existente admite reenvío TCP hasta el PostgreSQL configurado. Se probó únicamente la negociación de transporte: el backend no admite TLS, ni por acceso directo ni por SSH. SSH cifra el tramo Windows–`.55`, no el tramo final hacia PostgreSQL. Antes de autenticar, confirma que ese tramo privado es una ruta aprobada o usa un endpoint cifrado aprobado. No hagas un fallback automático a una conexión sin TLS. |
| NAS | `/mnt/cloud-imagenes` está montado realmente como NFS 4.1 `hard`. Cloud monta esa raíz en `/nas`; sus imágenes están en `/nas/images`. El catálogo activo se configura en `/app/image-state/.catalog.sqlite3`, en un bind de disco local distinto del NAS. Los estados de proveedores están bajo el bind local de `/app/data`. Véase el [corte al NAS](../deployment/nfs/PRODUCTION.md). |
| Permisos | En Windows no se encontró acceso al catálogo ni cliente Docker disponible. La identidad SSH no privilegiada de `.55` no pudo leer las rutas de imágenes, catálogo ni datos con `test -r`. Docker permite un bind independiente de solo lectura del montaje existente; falta verificar la lectura efectiva bajo la identidad del piloto. No modificar ACL, propietarios ni montajes productivos para conseguir acceso. |

### Ejecutar un piloto aislado

1. **Inspección sin secretos.** Usa `docker ps` para nombres y puertos; `docker inspect --format` para identidad, redes y montajes; `docker network inspect` para la red; y `findmnt -T /mnt/cloud-imagenes` para confirmar la exportación. No publiques `docker inspect` sin filtrar ni el Compose expandido: pueden contener credenciales. Comprueba los puertos desde el entorno de ejecución, no solo desde el host.
2. **Elegir entorno.** Usa Windows con la ruta PostgreSQL aprobada si también dispone de imágenes y catálogo de solo lectura. En caso contrario, prepara en `.55` un contenedor efímero independiente con la imagen o checkout del SHA candidato verificado. No reutilices el contenedor `abcd-control`, su entrypoint, su Compose ni su red por defecto. Empieza con `--network none` para verificar el almacenamiento; para Odoo usa únicamente una red aprobada con salida al backend. No publiques puertos para pruebas CLI. Si hace falta probar la interfaz, limita su acceso a loopback y al túnel SSH autorizado.
3. **Proteger el almacenamiento.** Usa `--read-only`, usuario no root autorizado para leer el catálogo y las imágenes, `--cap-drop ALL`, `--security-opt no-new-privileges` y límites de memoria y tiempo. Monta la raíz NFS existente con `--mount type=bind,src=/mnt/cloud-imagenes,dst=/nas,readonly`; monta el directorio real de catálogo local por separado, también `readonly`. No montes el directorio operativo completo por conveniencia: limita el acceso a los estados imprescindibles. Solo el espacio temporal aislado del piloto será escribible. Verifica los flags y que no exista fallback local antes de abrir archivos; no ensayes escrituras en datos reales.
4. **Abrir sin inicializar.** Usa las funciones de lectura del candidato, sin arranque productivo, migraciones, schedulers, monitor, creación de miniaturas o inicialización de repositorio. Abre el SQLite con `mode=ro` y `query_only`. Si el WAL impide abrir el catálogo bajo un bind de solo lectura, no pruebes con `immutable=1` sobre el catálogo activo ni copies su fichero principal por separado. Utiliza una instantánea coherente y autorizada del catálogo y los estados en almacenamiento aislado.
5. **Consultar y registrar.** Inyecta solo la configuración Odoo necesaria por el mecanismo seguro existente, sin mostrarla ni copiarla a Git, al historial de comandos o a opciones del contenedor que expongan los valores. Verifica permisos de lectura, `transaction_read_only`, timeout y tablas antes de consultar la muestra. Si la identidad existente no es apropiada, solicita una aprobada de mínimo privilegio. Ejecuta las pruebas de la sección siguiente. Retira solo el contenedor y los temporales del piloto; no cambies servicios ni montajes existentes.

**Resultado del preflight:** verificados el inventario Docker, el transporte PostgreSQL (no la autenticación) y el montaje NFS (no la lectura de originales). No se inició un contenedor de pruebas, no se consultaron pedidos, no se abrieron catálogos ni se accedió a portales. La validación funcional queda pendiente de confirmar la ruta final de Odoo y ejecutar el piloto con identidad y binds de lectura verificados.

## Panel de proveedores

| Recorrido | Evidencia mínima |
|---|---|
| P0.1 · Odoo y NAS | De uno a tres pedidos por proveedor con asociación comercial confirmada. Contrastar fechas, estado, pedido, líneas, cantidades, variante, EAN/UPC original, modelo, color y calibre. Verificar límites, filtros y paginación sin consultas masivas. Comprobar identidad, existencia, SHA-256, vistas técnicas, extras, cobertura e historial sin modificar catálogo ni originales. |
| P0.2 · Procesamiento | Validar primero preparación, selección, confirmación, locks, duplicados, reintentos, historial y resultados parciales sin efectos externos. Registrar por separado cualquier ejecución real, autorizada por proveedor, pedido, referencias y efectos. |
| P0.3 · Biblioteca y ZIP | Abrir imágenes reales, volver al pedido conservando filtros y comprobar identidad, vistas, integridad y exclusiones para Original, Farfetch y Miinto. No escribir en NAS durante la vista previa. Si se autoriza generar ZIP, usar almacenamiento temporal local aislado y verificar que no contiene originales antes de limpiarlo. |

Para Kering, no ampliar el piloto existente sin autorización. Para Luxottica, no consumir mensajes ni atribuir recepciones globales a pedidos sin evidencia. Para Marcolin, no reenviar solicitudes pendientes. Para Sáfilo y NuORDER, verificar el circuito asistido y los permisos sin asumir automatización ni API. Prueba importaciones únicamente en almacenamiento local aislado hasta obtener autorización para producir efectos.

## Registro y decisión

Por prueba, registra: fecha, entorno, SHA, proveedor, identificador de muestra no sensible, sistema real, operación, resultado esperado y obtenido, evidencia técnica, errores, correcciones y estado (**correcto**, **incorrecto** o **bloqueado**, con causa). Separa los resultados en cuatro grupos: offline con mocks, local con servicios reales, procesamiento real autorizado y verificación posterior al despliegue. No copies credenciales, archivos productivos ni datos identificativos al repositorio.

Para un defecto demostrado, reproduce, corrige en su rama, añade una regresión y vuelve a comprobar la operación real y las pruebas offline afectadas con el nuevo HEAD y su CI. Si no puedes repetir la prueba real, mantenla bloqueada.

Antes de fusionar, confirma el CI obligatorio, las aprobaciones, ausencia de conflictos y de defectos críticos, evidencia local del SHA y el estado del despliegue automático. Antes de desplegar, confirma además el SHA integrado, el digest verificado, los respaldos, el rollback, las automatizaciones y la autorización expresa. Sigue el [procedimiento de despliegue Cloud](../deployment/cloud/README.md).
