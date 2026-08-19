# 📱 Interfaz de la Aplicación - Qué Vas a Ver

## 1️⃣ Pantalla de Login

```
┌─────────────────────────────────────────────┐
│                                             │
│        DIAGONAL EYEWEAR                    │
│                                             │
│        Usuario: [diagonal_________]        │
│        Contraseña: [***********]           │
│                                             │
│        [ Iniciar Sesión ]                  │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 2️⃣ Menú Principal (Sidebar)

Verás este menú a la izquierda:

```
┌─────────────────────────────────────┐
│  📊 Diagonal Eyewear                │
│                                     │
│  ☰ NAVEGACIÓN PRINCIPAL             │
│  ├─ 📊 ABCD                         │
│  ├─ 📈 Análisis                     │
│  ├─ 🔧 Configuración                │
│  └─ ℹ️  Ayuda                       │
│                                     │
│  ☰ SECCIÓN MASTERDATA (cuando       │
│     seleccionas ABCD):              │
│  ├─ 📥 Importador Masterdata        │
│  └─ 🧪 Dry-run Odoo                │
│                                     │
└─────────────────────────────────────┘
```

---

## 3️⃣ Pantalla de Masterdata (Importador)

### 📊 Sección 1: Transformación

```
╔════════════════════════════════════════════╗
║  1. CARGAR EXCEL ORIGEN LUXOTTICA         ║
╚════════════════════════════════════════════╝

[ 📂 Sube el Excel origen ]  ← Click aquí

(Mostrará archivos si ya existen)
```

**Después de subir**, verás:

```
╔════════════════════════════════════════════╗
║  ⚠️  ADVERTENCIAS DE TRANSFORMACIÓN        ║
╠════════════════════════════════════════════╣
║  • Registros procesados: 14,176            ║
║  • Registros exportados: 12,782            ║
║  • Registros descartados: 1,394            ║
║                                            ║
║  ✓ Pérdida de ceros iniciales: 0           ║
║  ✓ Filas inválidas SI/NO: 0                ║
║  ! Marcas no resueltas: 1 (GENERICS)       ║
║                                            ║
║  Status: ✅ LISTO PARA USAR                ║
╚════════════════════════════════════════════╝
```

---

### 📥 Sección 2: Descargar Resultados

```
╔════════════════════════════════════════════╗
║  2. DESCARGAR RESULTADOS                  ║
╚════════════════════════════════════════════╝

[ 📥 Excel transformado ]
   └─ MASTERDATA_transformado.xlsx (18 cols)

[ 📥 JSON de validación ]
   └─ MASTERDATA_validacion.json

[ 📥 Resumen ejecutivo ]
   └─ MASTERDATA_resumen_ejecutivo.md

[ 📥 Auditoria descartes ]
   └─ *_descartes_auditoria.csv

[ 📥 Auditoria marcas ]
   └─ *_marcas_auditoria.csv

[ 📥 Resumen ceros iniciales ]
   └─ *_ceros_iniciales_resumen.csv
```

---

### 🎁 Sección 3: Solicitud de Imágenes Luxoptica

```
╔════════════════════════════════════════════╗
║  3. SOLICITUD DE IMÁGENES (LUXOPTICA)     ║
╚════════════════════════════════════════════╝

Email para la solicitud:
[ images@diagonaleyewear.com________]
  (ya está predeterminado)

Seleccionar whitelist de accesorios:
[✓] Aplicar whitelist de accesorios local

[ 🎁 GENERAR ARCHIVOS PARA PEDIR IMÁGENES ]
   
   Cuando hagas click, verás:
   ✅ Generados 1 archivo(s) en docs/Luxoptica/
   
   Si hubiera 500 EAN: Generaría 2 archivos (250 + 250)
   Si hubiera 1000 EAN: Generaría 4 archivos (250+250+250+250)
```

**Tabla de Lotes Generados:**

```
╔════════════════════════════════════════════════════════════╗
║  LOTES GENERADOS                                          ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  📄 upc-products-images-request-20260819_102506-50-e...   ║
║     │                                                      ║
║     ├─ EAN totales: 50                                    ║
║     ├─ [ 📥 Descargar lote 1 (50 EAN) ]                   ║
║     └─ [ ✅ Marcar como procesado ]                       ║
║                                                            ║
║  Progreso: 0/1 lotes procesados                           ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
```

---

## ✅ Flujo en Tiempo Real

### Paso 1: Subir Excel
```
Usuario: Click en "Sube el Excel origen"
Sistema: Selecciona archivo
Sistema: Muestra barra de progreso
Usuario: Espera 30-60 segundos
Sistema: Muestra advertencias
```

### Paso 2: Generar Lotes
```
Usuario: Verifica email (images@diagonaleyewear.com)
Usuario: Click "Generar archivos"
Sistema: Procesa datos (5-10 segundos)
Sistema: Crea archivos .txt
Sistema: Muestra tabla de lotes
```

### Paso 3: Descargar Lotes
```
Usuario: Click "Descargar lote 1"
Sistema: Descarga archivo .txt
Usuario: Guarda archivo
```

### Paso 4: Marcar Procesado
```
Usuario: Click "Marcar como procesado"
Sistema: Registra en estado interno
Sistema: Actualiza progreso (1/1)
```

---

## 🌍 Después de Enviar a Luxoptica

Vuelve a la app en 24-48h. Verás:

```
╔════════════════════════════════════════════╗
║  🤖 DESCARGA AUTOMÁTICA                   ║
╚════════════════════════════════════════════╝

Si hay imágenes descargadas:

[ 🧪 DRY-RUN ODOO ]
   └─ Botón para validar importación

[✅] Imágenes descargadas: 50
[ ] Ubicación: docs/Luxoptica/descargas/
               2026-08-19/lote-001/
```

---

## 💡 Tips Importantes

1. **Email es crítico**: `images@diagonaleyewear.com`
   - Sin este, no recibirás las imágenes

2. **Tamaño de lotes**: Máximo 250 EAN por archivo
   - Luxoptica requiere esto

3. **Descargas automáticas**: Cada hora
   - No necesitas hacer nada
   - Log: `.mail_download_state.json`

4. **Revisa advertencias**:
   - Si ves marcas no resueltas: revisar auditoria
   - Si ves pérdida de ceros: investigar

---

## 🎯 Resumen Ejecutivo

| Acción | Tiempo | Manual/Automático |
|--------|--------|-------------------|
| Subir Excel | 30-60s | Manual |
| Transformar | 5-10s | Automático |
| Generar lotes | 5-10s | Manual |
| Descargar .txt | 5s | Manual |
| Enviar a Luxoptica | 2-3min | Manual |
| Marcar procesado | 1s | Manual |
| **Luxoptica procesa** | **24-48h** | **Manual (Luxoptica)** |
| Descarga imágenes | 1-5min | Automático (Graph API) |
| **Total** | **~24-48h** | **80% automático** |

---

**🚀 ¡Ahora abre http://localhost:8501 en tu navegador y comienza el flujo!**
