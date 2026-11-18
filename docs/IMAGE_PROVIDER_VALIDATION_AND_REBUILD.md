# Proveedores, seleccion de exportacion y reconstruccion futura

Plan del 17/11/2026. **No se ha accedido a portales ni ejecutado este plan.**
Ninguna fusion de codigo autoriza envios, descargas, importaciones, pausa de
servicios, modificacion de datos o destruccion. La recuperabilidad de todas las
imagenes es una hipotesis operativa que se debe demostrar antes de retirar datos.

## Galeria implementada y seleccion propuesta

La biblioteca muestra fotografias unicas por EAN/checksum, conservando todas las
representaciones. Detalle, lifestyle y desconocidas no desaparecen por no ser
V1/V2/V3. Fotografias distintas de una misma vista se conservan. Se senalan
coincidencias de SHA/huella entre EAN sin cambiar sus asociaciones.

La huella registrada es SHA-256 de RGB normalizado por EXIF y redimensionado a
64x64; no es una busqueda perceptual aproximada. La coincidencia no acredita
identidad de variante ni color. El catalogo historico puede no tener modelo/color:
la tabla de trazabilidad deja el dato sin completar y requiere contraste externo.

**Propuesta para una galeria seleccionada de exportacion, no implementada:**

1. Separar visualmente "Originales" (galeria actual) de "Seleccion de exportacion".
2. Seleccionar explicitamente por producto/variante y checksum, no por indice,
   nombre ni vista. Permitir varias alternativas de una vista.
3. La seleccion guardaria solo referencias, criterio/canal y revision; nunca
   moveria, borraria o reclasificaria originales. Una foto no seleccionada sigue
   disponible. Cambios de contenido invalidan la seleccion hasta revision.
4. Mostrar en la seleccion EAN, modelo/color conocidos, vista y vinculo a todas
   las procedencias. Los conflictos entre colores bloquean la aprobacion, no la
   consulta de originales.
5. Exportacion seleccionada con nombre distinto y manifiesto de assets,
   representaciones, checksums y decision de seleccion. Mantener el ZIP completo
   actual como operacion separada y compatible.
6. Reutilizar la publicacion comercial existente en `market_publication.py` para
   copias de canal, sin escribir en el catalogo canonico ni transmitir al destino
   remoto sin autorizacion. No crear un segundo repositorio de originales.

Requiere aprobar alcance, persistencia y permisos antes de implementar seleccion.
La mejora actual no crea selecciones ni altera el formato de exportacion.

## Validacion pequena por proveedor

Las lecturas Odoo prueban identidad y proveedores comerciales, no acceso a
portales. Una compra no decide por si sola el proveedor de imagenes. Las rutas
deben conservar ambito de variante/marca/sociedad/vigencia y evidencia; faltas o
contradicciones quedan en revision.

| Proveedor | Evidencia actual y limite | Siguiente prueba propuesta |
|---|---|---|
| Luxottica | Recibos correctos recientes, siete correos revisados, cero adjuntos nuevos en la muestra de auditoria | Observar siguiente entrega ordinaria y su recibo; no reprocesar correos ni modificar monitor |
| Kering | Worker antiguo, siete timeouts en redireccion; limites en main pero no en ese worker | Plan de mantenimiento separado; sonda de acceso y piloto de un EAN solo tras permisos independientes |
| Marcolin | Solicitud 1 local ready, remote_id nulo; seis UPC, cuatro referencias modelo/color y seis calibres | Cotejo de solo lectura autorizado en portal; no reenviar ni actualizar solicitud 1; nueva solicitud reducida con permiso distinto |
| Safilo | 603 contrastado con Carrera, Carolina Herrera y David Beckham; acceso/archivo real no probados | 1-2 productos Omnia, lista EAN y manifiesto de respuesta, luego ZIP en staging |
| NuORDER | Marcas/grupos comunicados; cuenta, permisos y formatos no contrastados | Acceso minorista autorizado; un producto por grupo en fases, descarga manual trazable posterior |

### Marcolin, solicitud 1 intacta

Los codigos auditados son `664689520060`, `664689520084`, `664689628810`,
`664689628827`, `664689764686`, `664689897605`. Odoo los vincula comercialmente a
573. Compartir cuatro referencias es compatible con el relato historico del
portal pero no demuestra identidad de solicitud.

Para reconciliar, primero obtener evidencia remota autorizada: ID, fechas,
cuenta/grupo, codigos, referencias agrupadas, activos/inactivos y acuse.
Comparar con la solicitud local sin cambiarla. Si no basta, dejar "correspondencia
no demostrada"; no inventar remote_id, no reenviar y no modificar fechas/estado.
La actualizacion posterior de una asociacion requeriria permiso de escritura
distinto, que esta intervencion no tiene.

Para una nueva prueba se proponen `889214415530` y `889214716408`, de 573 y
compras contrastadas. Revalidar cobertura y solicitudes vivas antes de crearla.
Conservar todos los UPC/calibres aunque el portal agrupe referencias.

### Safilo Omnia

Procedimiento comunicado por el cliente, aun pendiente de prueba real:
PRODUCTS/ASSETS -> busqueda avanzada -> **EAN CODE** en el bloque SKU ->
codigos separados por comas -> Search -> Add to selection -> seleccionar todos
-> download all selected. No confundir EAN CODE con SKU/UPC ni usar la descarga
de ficha individual como prueba de descarga multivista.

Candidatos de Odoo: `197737379230` y `197737181055`. Confirmar la forma GTIN
aceptada por EAN CODE; conservar el barcode original y documentar cualquier
equivalencia UPC/EAN, sin cambiar de producto. Probar primero uno y despues varios.
Guardar consultados/encontrados/no encontrados. Exigir manifiesto o evidencia
fichero-EAN-vista: los nombres opacos no autorizan un renombrado arbitrario.
El importador estricto debe dejar ambiguos en revision.

### NuORDER

Grupos comunicados: Dita, Cutler, Akoni/Balmain, Linda Farrow/Jacquemus.
La cuenta minorista debe demostrar que ve cada grupo y que puede descargar.
No asumir API, ZIP, formato, disponibilidad de originales ni todas las vistas.

Muestra candidata: Dita `0810187664441`, Cutler `5056322142671`, Akoni
`7630449264317`, Balmain `7630449263457`, Linda Farrow `5054275159913` y
Jacquemus `5054275164535`. Revalidar antes de la prueba; mantener ceros.
Akoni/Balmain tienen compras reales con 518 y 405425; no imponer un unico partner.
Linda Farrow/Jacquemus comparten 568 en la muestra, no por inferencia de nombre.

Conservar identificador de producto de portal, grupo, GTIN/SKU, modelo/color,
formatos, vistas, nombres originales y checksums. No guardar cookies/tokens.

### Autorizaciones y criterios comunes

1. **Acceso/consulta** por proveedor: permite login y consulta acotada, no
   solicitud, descarga ni modificacion del estado local.
2. **Envio Marcolin**: permiso especifico para una nueva solicitud y sus codigos.
3. **Descarga** por proveedor/grupo/lote: distinta del acceso o envio.
4. **Importacion a staging**: entorno aislado; no implica catalogo definitivo.
5. **Activacion productiva**: solo despues de aceptar el circuito completo.

Limitar cada piloto a los productos aprobados y una ejecucion. Aceptacion:
100 % de archivos aceptados con identidad demostrable, SHA y origen conservados,
vistas sustentadas en evidencia, cero nuevos assets al repetir el mismo
EAN/checksum y todos los ambiguos en revision. Archivos diferentes de una misma
vista no se descartan. V2 realmente ausente se documenta como parcial; detalle o
lifestyle nunca la sustituyen. No repetir indefinidamente una prueba fallida.

Separar imagenes, estado/catalogo SQLite, datos de proveedor/Kering, actividad,
work, backups y miniaturas de staging. No basta cambiar la raiz de fotografias.
No montar produccion en escritura. Ningun piloto procesa un pedido completo por
tener un EAN seleccionado.

## Reconstruccion definitiva: secuencia con puertas de aprobacion

1. **Inventario inicial.** Enumerar assets, representaciones, EAN/variantes,
   procedencias, vistas, hashes, originales no indexados, ZIP/manifiestos,
   solicitudes vivas, historial y estado de correo. Registrar faltantes y
   barcodes historicos sin coincidencia actual en Odoo.
2. **Backup y recuperacion demostrada.** Copias coherentes de todas las SQLite
   activas respetando WAL, config cifrada y custodia independiente de claves.
   Backup de originales fuera del mismo dominio de fallo del NAS. Verificar
   checksums y restaurar sobre copia aislada; `/nas/backups` no basta por si solo.
3. **Recuperabilidad por proveedor.** Probar los procedimientos definitivos de
   los cinco proveedores y comprobar retencion de correos/ZIP y productos
   historicos. Si falta fuente o alguna imagen no puede recuperarse, no retirar
   su original. No afirmar recuperacion completa solo por una muestra exitosa.
4. **Detencion controlada autorizada.** Inventariar UI/importadores/Graph/Kering,
   jobs externos y locks; pausar y drenar incorporaciones antes del corte.
   Conservar solicitudes y acuses para no duplicar envios ni reprocesar correo.
   No se ha detenido ningun servicio durante esta preparacion.
5. **Catalogo nuevo y vacio, paralelo.** Crear una generacion distinta con fotos,
   estado y datos compatibles. La generacion actual no se vacia. Validar mounts,
   UID, NFS, espacio, locks, replicas y versiones de todos los escritores.
6. **EAN y rutas desde Odoo, solo lectura.** Usar el contrato real de
   supplierinfo y distinguir plantilla/variante, calibre, marca/modelo/color,
   sociedad/vigencia y proveedor comercial/portal. Revision ante ambiguedad;
   no usar la ultima compra como ruta automatica.
7. **Recuperacion por proveedor.** Lotes pequenos a staging, con recibos,
   manifestados e idempotencia. Confirmar identidad antes de asociar al catalogo
   nuevo; ningun hash o nombre de fichero la sustituye.
8. **Clasificacion y deduplicacion.** Conservar alternativas por vista,
   detalle/lifestyle/unknown separados y procedencias completas. Deduplicar
   EAN/checksum y alias de presentacion, no eliminar por vista. Revisar
   coincidencias entre colores antes de aprobarlas. No fusionar productos por
   modelo ni huella visual.
9. **Cobertura por producto.** Comparar alcance Odoo y excepciones historicas,
   fotografias unicas, vistas verificables y calidad. Requerir recuperacion del
   100 % del alcance aprobado o excepciones expresamente aceptadas. Cero rutas
   dudosas aprobadas por defecto; V2 ausente declarada, no fingida.
10. **Exportaciones.** Probar ZIP completo con alias/manifiesto, originales,
    biblioteca, busqueda masiva y publicacion de canal en staging. Mantener los
    nombres/extensiones historicos y comprobar checksums de originales.
11. **Activacion reversible autorizada.** Captura final/delta coherente y
    cambio coordinado de app y escritores a la nueva generacion; no asumir que
    existe un symlink active. Verificar version, salud y ausencia de dos
    escritores apuntando a generaciones incompatibles.
12. **Observacion y rollback.** Conservar la generacion anterior intacta.
    Ensayar retorno conjunto de mounts/config/estados, sin restaurar SQLite
    activa ni mezclar estados de correo. Cualquier perdida de identidad,
    cobertura o compatibilidad detiene el corte.
13. **Retirada posterior.** Solo tras plazo acordado, backup/restauracion
    comprobados y autorizacion destructiva explicita. Sin esa puerta no hay
    borrado ni limpieza masiva.

Orden: Odoo -> catalogo/anomalias -> Kering -> Marcolin -> Safilo -> NuORDER.
Luxottica continua sin cambios funcionales. Las issues #38 y #59 siguen abiertas.
