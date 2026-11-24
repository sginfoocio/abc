# 🤖 Descarga Automática de Imágenes - Guía de Scripts

> **Referencia, no evidencia de operación.** La presencia de scripts o un servicio en Compose no demuestra que Graph, el buzón o las descargas estén funcionando. No ejecutes lecturas de Graph, descargas, tareas programadas ni cambios de servicios productivos sin autorización específica. Sigue la [validación local de Cloud](../CLOUD_LOCAL_VALIDATION.md).

## 📋 Tres Scripts Principales

### 1️⃣ `monitor_luxoptica_downloads.py`
**Propósito documentado:** inspeccionar configuración y estado. Puede autenticar contra Graph;
úsalo únicamente en un entorno y alcance autorizados. No imprimas su salida si contiene datos
operativos.

---

### 2️⃣ `run_luxoptica_download.py`
**Propósito:** leer el buzón y descargar adjuntos. Su ejecución puede leer datos reales y escribir
archivos/estado; requiere autorización específica previa.

**Qué hace:**
1. Valida configuración M365
2. Se conecta a Microsoft Graph API
3. Lee inbox del mailbox compartido
4. Descarga adjuntos (imágenes)
5. Organiza por modelo y EAN
6. Registra aliases de Farfetch y Miinto cuando existen sus IDs en Odoo
7. Registra pendientes en `repo/images/.market_pending.json`
8. Registra correos procesados en `.mail_download_state.json`

---

### 3️⃣ `scheduler_luxoptica.py`
**Estado:** Configurador histórico de una tarea local; no es el monitor vigente y no debe activarse.

### Monitor automático en Docker

El procedimiento versionado documenta un intervalo de 10 minutos, pero no acredita el estado
del servicio ni su configuración actual. No reconstruyas, recrees ni inicies el contenedor desde
esta guía. Verifica los procesos existentes de forma local, de solo lectura y con autorización.

---

## Flujo de validación

1. Preparar las solicitudes desde MASTERDATA sin enviar al portal.
2. Obtener autorización específica para cualquier lectura real de Graph, descarga o importación.
3. Seguir la [política de validación local Cloud](../CLOUD_LOCAL_VALIDATION.md), comenzar por
   lecturas sin efectos y aislar las escrituras locales.
4. Registrar resultado por SHA y marcar como bloqueada cualquier dependencia real que no pueda
   comprobarse. No inferir éxito por mensajes de ejemplo o un contenedor activo.

---

## 📊 Almacenamiento canónico

```
IMAGE_REPOSITORY_ROOT/
└─ 8056262672563/
   └─ originales.png
```

Si una imagen ya se ha descargado pero Odoo todavía no tiene el ID de
Farfetch o Miinto, el original se conserva en su carpeta EAN y se registra en
`.market_pending.json` bajo el estado local del repositorio. El monitor vuelve a consultar Odoo en cada intervalo
(`--interval-minutes`, 10 minutos por defecto) y completa los aliases del
catálogo cuando los IDs aparecen. Las copias persistentes se preparan por
separado con `market_publication.py`; el comando simula por defecto y no activa
la sincronización remota.

La publicación persistente usa `/mnt/cloud-imagenes/market-publication/` con
`Farfetch/<EAN>/<ID>_Vn.png` y `Miinto/<EAN>/<ID>_Vn.jpeg`. No forma parte del
árbol canónico mostrado arriba.

---

## NAS Synology: sincronización de imágenes

Las imágenes organizadas en `repo/images/` se sincronizan con el Synology
mediante el servicio rsync. El módulo remoto validado es `Fotos` y el destino
corresponde a la carpeta compartida `/volume1/Fotos`.

Esta replica remota se conserva, pero **no equivale al backup completo de
migracion**: excluye ZIP y no verifica por si misma planes/recibos/recuperacion.
En la comprobacion del 10/10/2026 desde Cloud 192.168.1.55, el acceso TCP 873 y
el listado rsync agotaron su timeout. No se escribio en el NAS remoto.
Preparacion NFS y backups independientes:
[plan de almacenamiento](../../deployment/nfs/README.md).
Con el layout separado, los adjuntos voluminosos van a
`IMAGE_REPOSITORY_WORK_ROOT`; JSON de estado y SQLite permanecen locales en
`IMAGE_REPOSITORY_STATE_ROOT`. No sincronizar estas raices ni locks/metadatos
privados al modulo publico Fotos. Antes del corte, revisar el origen de la
tarea rsync para la nueva raiz por EAN manteniendo la replica, sin `--delete`.

### Comprobar conectividad y módulo

Desde el equipo que realiza la sincronización:

```powershell
Test-NetConnection 188.227.143.110 -Port 873
rsync rsync://188.227.143.110/
```

Debe aparecer el módulo `Fotos`. La conexión rsync usa el usuario configurado
en Synology, por ejemplo `sync_images`.

### Prueba sin modificar el NAS

Antes de copiar datos, ejecutar un `dry-run`:

```powershell
rsync -avn --itemize-changes --stats `
   --exclude='staging/' `
   --exclude='20??-??-??/***' `
   --exclude='*.zip' `
   repo/images/ `
   sync_images@188.227.143.110::Fotos/
```

Las exclusiones evitan copiar las carpetas temporales por fecha y los ZIP
descargados. En la prueba validada se detectaron 1.202 archivos y unos 3,77 GB
para sincronizar, sin borrar archivos remotos.

### Sincronización real

Cuando el `dry-run` sea correcto:

```powershell
rsync -av --partial `
   --exclude='staging/' `
   --exclude='20??-??-??/***' `
   --exclude='*.zip' `
   repo/images/ `
   sync_images@188.227.143.110::Fotos/
```

`--partial` permite reanudar transferencias interrumpidas. No se usa `--delete`,
por lo que la sincronización no elimina contenido existente en el NAS.

`staging/` se excluye en todos los niveles: nunca replicar fuente, backups,
recuperacion ni temporales del ensayo. Estos comandos son una receta, no
prueba de una tarea automatica. La investigacion encontro dos comandos
manuales historicos con origen relativo `repo/images/` y sin exclusion literal
staging; no se localizo script/programacion de replica en el alcance accesible.
No se presupone DSM ni se cambio o ejecuto la replica. La nueva replica Fotos
se preparara tras validar migracion, con simulacion/conectividad previas,
solo imagenes definitivas por EAN, sin staging/backups/temporales/SQLite,
sin borrados, con lock e historial visible Cloud; no activar ahora.
Evidencia y limites: [captura consistente](../../deployment/nfs/CAPTURE.md).

### Requisitos del Synology

- Servicio rsync habilitado.
- Módulo compartido `Fotos` publicado.
- Usuario rsync con permiso de lectura/escritura en `Fotos`.
- Puerto TCP `873` accesible desde el servidor Docker.
- Si el acceso se realiza por Internet, limitar la regla WAN a la IP pública
   del servidor y preferir una VPN frente a exponer rsync directamente.

---

## 🔍 Logs y Diagnostico

No borres ni reinicies el estado de correo: eso puede volver a procesar mensajes y duplicar
descargas. Inspecciona recibos y logs solo con permisos autorizados; no publiques identificadores
de correo, contenido, rutas ni secretos.

---

## ✅ Checklist de Verificación

- [ ] Revisar configuración por mecanismos aprobados sin imprimir secretos.
- [ ] Confirmar por separado estado, identidad, intervalos y recibos del monitor.
- [ ] Ejecutar lectura/descarga solo con autorización y alcance definidos.
- [ ] Registrar SHA, entorno, muestra, resultado y evidencia sin datos sensibles.
- [ ] Si no se puede validar una dependencia real, marcarla bloqueada.

---

## 🎯 Resumen

| Script | Propósito | Ejecutar | Frecuencia |
|--------|-----------|----------|-----------|
| `test_graph_api_config.py` | Comprobación técnica | Solo entorno y alcance autorizados | No inferir |
| `monitor_luxoptica_downloads.py` | Inspeccionar estado | Solo entorno autorizado | Runtime no verificado |
| `run_luxoptica_download.py` | Leer correo y descargar | Requiere autorización específica | Manual |
| `poll_luxoptica_mail.py` | Monitor documentado | No iniciar otra instancia | 10 min documentados; runtime no verificado |
| `scheduler_luxoptica.py` | Configurador histórico | No activar | No aplica |

---

## 📞 Troubleshooting

### Error: "Faltan variables de entorno"
```powershell
# No imprimas el contenido de .env.
# Comprueba presencia/configuración con el mecanismo seguro aprobado.
```

### Error: "403 Forbidden"
No cambies permisos de Azure/Exchange para resolverlo desde esta guía. Solicita revisión a la
persona administradora y autorización específica antes de cualquier reintento.

### No descarga las imágenes
No infieras que el buzón está vacío ni que Luxottica no ha respondido sin una lectura autorizada.
Verifica el monitor, el intervalo y los recibos de acuerdo con la guía de validación.

---

## 🎉 Próximos Pasos

1. Revisa y prepara un lote manual en la app.
2. Obtén autorización para cualquier envío remoto, lectura de Graph o descarga.
3. Verifica los resultados localmente y conserva evidencia para el SHA evaluado.
4. No crees una tarea programada ni un segundo monitor.

---

La réplica al NAS y la publicación remota no forman parte de la preparación de solicitudes.
No ejecutes ni configures esos procesos desde esta guía.
