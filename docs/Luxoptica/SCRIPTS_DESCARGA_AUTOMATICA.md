# 🤖 Descarga Automática de Imágenes - Guía de Scripts

## 📋 Tres Scripts Principales

### 1️⃣ `monitor_luxoptica_downloads.py`
**Propósito:** Monitorear estado y logs

```powershell
python monitor_luxoptica_downloads.py
```

**Qué revisa:**
- ✅ Variables de configuración M365 en .env
- ✅ Conectividad con Graph API (autenticación)
- ✅ Archivo de estado de descargas
- ✅ Imágenes ya descargadas

**Output:**
```
✅ Configuración: OK
✅ Conectividad Graph: OK
✅ Estado de descargas: 5 correos procesados
✅ Carpeta de descargas: 50 imágenes en lote-001
```

**Cuándo usarlo:** 
- Verificar que todo está funcionando
- Revisar qué imágenes se descargaron
- Diagnosticar problemas

---

### 2️⃣ `run_luxoptica_download.py`
**Propósito:** Ejecutar descarga de imágenes (manual/test)

```powershell
python run_luxoptica_download.py
```

**Qué hace:**
1. Valida configuración M365
2. Se conecta a Microsoft Graph API
3. Lee inbox del mailbox compartido
4. Descarga adjuntos (imágenes)
5. Organiza por lote y fecha
6. Registra en `.mail_download_state.json`

**Output:**
```
✅ Correos escaneados: 15
✅ Con adjuntos: 1
✅ Adjuntos descargados: 50
   Guardados en: docs/Luxoptica/descargas/2026-08-20/lote-001/
```

**Cuándo usarlo:**
- Test manual para verificar que funciona
- Ejecutar manualmente cuando sea necesario
- Debugging de problemas

---

### 3️⃣ `scheduler_luxoptica.py`
**Propósito:** Configurar ejecución automática (cada hora)

```powershell
# Ver instrucciones para crear scheduler
python scheduler_luxoptica.py setup

# Ejecutar descarga manual (test)
python scheduler_luxoptica.py test

# Ver estado actual
python scheduler_luxoptica.py status
```

**Qué hace:**
- Proporciona comando PowerShell para crear tarea en Windows Task Scheduler
- Permite ejecutar pruebas
- Muestra estado actual

**Instalación de Scheduler (Windows):**
```powershell
# Copiar el comando que genera:
python scheduler_luxoptica.py setup

# Ejecutar en PowerShell (como Administrador)
[Copiar y pegar el comando generado]

# Verificar que se creó
Get-ScheduledTask -TaskName "LuxopticaImageDownloader"
```

---

## 🚀 Flujo Recomendado

### Día 1: Envío a Luxoptica (hoy)
```powershell
# 1. Verificar que todo está ok
python monitor_luxoptica_downloads.py

# Debe mostrar:
# ✅ Configuración: OK
# ✅ Conectividad Graph: OK
# ✅ Carpeta de descargas: vacía (esperando respuesta)
```

### Día 2-3: Esperar Respuesta Luxoptica
- Luxoptica procesa (24-48h)
- Envía correo a `images@diagonaleyewear.com`

### Día 3+: Descargar Automáticamente

**Opción A - Manual (Test Primero):**
```powershell
# Test 1: Ejecutar descarga manual
python run_luxoptica_download.py

# Output esperado:
# ✅ Correos escaneados: 5
# ✅ Adjuntos descargados: 50
# 📁 Guardados en: docs/Luxoptica/descargas/2026-08-20/lote-001/

# Test 2: Monitorear estado
python monitor_luxoptica_downloads.py

# Output esperado:
# ✅ Estado de descargas: 50 imágenes descargadas
# 📁 docs/Luxoptica/descargas/2026-08-20/lote-001/
#    ├─ image-001.jpg
#    ├─ image-002.jpg
#    └─ ...
```

**Opción B - Automático (después de verificar):**
```powershell
# Crear tarea de Windows que ejecute cada hora
python scheduler_luxoptica.py setup

# Ver el comando PowerShell generado
# Copiar y ejecutar en PowerShell (Admin)

# Verificar que se creó
Get-ScheduledTask -TaskName "LuxopticaImageDownloader"

# Verificar que funciona
python scheduler_luxoptica.py status
```

---

## 📊 Estructura de Archivos Descargados

```
docs/Luxoptica/descargas/
├─ .mail_download_state.json          (log de correos procesados)
└─ 2026-08-20/                         (fecha de recepción del correo)
   └─ lote-001/                        (número de lote)
      ├─ image-001.jpg
      ├─ image-002.jpg
      ├─ image-003.png
      └─ ...
```

---

## 🔍 Logs y Diagnostico

### Ver qué correos se procesaron
```powershell
Get-Content docs/Luxoptica/descargas/.mail_download_state.json | ConvertFrom-Json | Select -ExpandProperty processed_message_ids
```

### Ver último resultado de ejecución
```powershell
# Si está en Windows Task Scheduler, revisar Event Viewer
Get-EventLog -LogName Application -Source TaskScheduler -Newest 5
```

### Limpiar logs (si es necesario)
```powershell
# Resetear el estado (descargará todo de nuevo)
Remove-Item docs/Luxoptica/descargas/.mail_download_state.json -Force
```

---

## ✅ Checklist de Verificación

- [ ] `.env` tiene todas las 5 variables M365
- [ ] `python test_graph_api_config.py` muestra ✅ TODAS LAS PRUEBAS PASARON
- [ ] `python monitor_luxoptica_downloads.py` muestra 4x ✅
- [ ] `python run_luxoptica_download.py` funciona sin errores (aún sin imágenes)
- [ ] Windows Task Scheduler configurado (opcional pero recomendado)
- [ ] Solicitud enviada a Luxoptica el 2026-08-19
- [ ] Esperando respuesta (24-48h)

---

## 🎯 Resumen

| Script | Propósito | Ejecutar | Frecuencia |
|--------|-----------|----------|-----------|
| `test_graph_api_config.py` | Validar credenciales | 1x (setup) | Manual |
| `monitor_luxoptica_downloads.py` | Ver estado | Bajo demanda | Manual |
| `run_luxoptica_download.py` | Descargar imágenes | Test y manual | Manual |
| `scheduler_luxoptica.py` | Automatizar | Setup 1x | Automático (cada hora) |

---

## 📞 Troubleshooting

### Error: "Faltan variables de entorno"
```powershell
# Verificar .env
cat .env | Select-String "M365_"

# Debe mostrar 5 líneas con M365_
```

### Error: "403 Forbidden"
```powershell
# Permisos no concedidos en Azure AD
# Ejecutar en PowerShell (como admin):
python test_graph_api_config.py

# Revisar: docs/Luxoptica/CONFIGURACION_GRAPH_API.md Paso 3-4
```

### No descarga las imágenes
```powershell
# Verificar que hay correos en mailbox
# (Luxoptica aún no ha respondido - esperar 24-48h)

# Ver estado actual
python monitor_luxoptica_downloads.py

# Si muestra "Carpeta: vacía" - es normal, esperar a Luxoptica
```

---

## 🎉 Próximos Pasos

1. ✅ Ejecuta hoy: `python monitor_luxoptica_downloads.py`
2. ⏳ Espera 24-48h a que Luxoptica responda
3. 🔄 Ejecuta: `python run_luxoptica_download.py` (cuando llegue el correo)
4. ✅ Verifica: `python monitor_luxoptica_downloads.py` (debe mostrar imágenes)
5. 🤖 Configura: Scheduler automático si todo funciona bien

---

**Commit:** Todos estos scripts están en repositorio `main` y sincronizados.
