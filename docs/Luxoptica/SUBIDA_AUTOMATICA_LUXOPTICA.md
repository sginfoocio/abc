# 🚀 Guía Rápida: Subida Automática a Luxoptica

## Configuración Rápida (2 pasos)

### Paso 1: Agregar credenciales a `.env`

Abre `.env` y agrega al final:

```env
# Luxoptica - Subida automática
LUXOPTICA_URL=https://portal.luxottica.com/
LUXOPTICA_USERNAME=tu_usuario_luxoptica
LUXOPTICA_PASSWORD=tu_contraseña_luxoptica
LUXOPTICA_REQUEST_EMAIL=images@diagonaleyewear.com
```

### Paso 2: Generar lote y subir desde app

1. **Abre app**: http://localhost:8501
2. **Login** en la app
3. **ABCD** → **Importador Masterdata**
4. Sube Excel de Luxoptica
5. **Generar lote de prueba (50 EAN)** o **Generar lotes completos**
6. **🚀 Subir a Luxoptica Automáticamente** ← Nuevo botón

El script automáticamente:
- ✅ Se autentica en Luxoptica
- ✅ Navega a Imágenes de Producto  
- ✅ Carga el fichero .txt
- ✅ Selecciona "Todas las vistas"
- ✅ Especifica email: images@diagonaleyewear.com
- ✅ Envía la solicitud

---

## Alternativa Manual (Línea de Comandos)

```powershell
python luxoptica_auto_upload.py
```

**Requisitos**:
- `.env` configurado con credenciales Luxoptica
- Fichero de EAN en `docs/Luxoptica/`

**Output esperado**:
```
🌐 Iniciando navegador...
🔐 Autenticando...
📸 Navegando a sección de Imágenes...
📤 Subiendo fichero...
✓ Seleccionando opciones...
📧 Configurando email...
🚀 Enviando solicitud...

✅ Solicitud enviada exitosamente a images@diagonaleyewear.com
```

---

## Flujo Completo Desde App

```
┌─────────────────────────────────────────────┐
│  1. Subir Excel origen                      │
│     ↓                                       │
│  2. Transformar MASTERDATA                  │
│     ↓                                       │
│  3. Generar lote (50 o todos)               │
│     ↓                                       │
│  4. 🚀 SUBIR A LUXOPTICA (AUTOMÁTICO)       │
│     ├─ Login                                │
│     ├─ Cargar fichero                       │
│     ├─ Seleccionar opciones                 │
│     └─ Enviar                               │
│     ↓                                       │
│  5. ⏳ Esperar respuesta (10 min - 48h)     │
│     ↓                                       │
│  6. 🤖 Descarga automática de imágenes      │
│     ├─ Detecta correo                       │
│     └─ Descarga adjuntos                    │
│     ↓                                       │
│  7. 📁 Imágenes en docs/Luxoptica/descargas/│
└─────────────────────────────────────────────┘
```

---

## ✅ Verificación

### Si ves error "403 Forbidden"
- Verifica que el usuario y contraseña son correctos
- Intenta login manualmente en Luxoptica primero

### Si dice "Playwright no instalado"
```powershell
pip install playwright
python -m playwright install chromium
```

### Si no encuentra la sección de Imágenes
- El portal puede tener interfaz diferente
- Revisa que el usuario tiene permisos para "Recursos Digitales"
- Prueba manualmente una vez para verificar el flujo

---

## 🎯 Timing

| Paso | Tiempo | Manual/Automático |
|------|--------|-------------------|
| Transformar | 5-10s | Auto |
| Generar lote | 5-10s | Manual |
| Subir a Luxoptica | 2-5 min | **Auto (nuevo)** |
| Luxoptica procesa | 10 min - 48h | Manual (Luxoptica) |
| Descargar imágenes | 1-5 min | Auto (Graph API) |
| **Total** | **~5-50 min** | **80% Automático** |

---

## 📋 Script Detallado

`luxoptica_auto_upload.py` hace esto:

```python
1. Leer .env (credenciales)
2. Buscar fichero más reciente en docs/Luxoptica/
3. Abrir navegador (Playwright)
4. Navegar a https://portal.luxottica.com/
5. Rellenar email/usuario
6. Rellenar contraseña
7. Hacer click en "Continue" / "Continuar"
8. Esperar a cargar
9. Hacer click en Servicios
10. Hacer click en Recursos Digitales
11. Hacer click en Imágenes de Producto
12. Subir fichero .txt
13. Seleccionar checkbox "Todas las vistas"
14. Rellenar email destino
15. Hacer click en "Enviar"
16. Cerrar navegador
17. Reportar éxito
```

---

## 🔐 Seguridad

- ❌ NO guardar credenciales en código
- ✅ Guardar en `.env` (no commiteado)
- ✅ El navegador se abre y cierra automáticamente
- ✅ Sin credenciales guardadas en el navegador

---

## 🆘 Troubleshooting

### Script no encuentra el campo de usuario
- El portal puede tener selectores diferentes
- Abre el navegador manualmente: `python luxoptica_auto_upload.py` con `headless=False`
- Verifica los selectores en el código y actualiza si es necesario

### Falla en "Todas las vistas"
- El checkbox puede tener otro nombre
- Revisa el HTML del portal manualmente
- Actualiza los selectores en el script

### Necesita 2FA / Autenticación adicional
- El script actual no soporta 2FA
- Configura Luxoptica para desactivar 2FA para esta cuenta de servicio (si es posible)
- O deja que falle y completa manualmente

---

## 📞 Soporte

Si algo falla, ejecuta con navegador visible:

```python
# En el código, cambia:
headless=False,  # Ver navegador mientras se ejecuta
```

Así puedes ver exactamente dónde se atasca el script.

---

**Estado**: ✅ Listo para usar
**Commit**: `luxoptica_auto_upload.py` + botón en app
