# 🎯 Guía de Flujo Completo: Solicitud de Imágenes Luxoptica

## 🌐 Acceso a la Aplicación

Abre el entorno local autorizado de la aplicación e inicia sesión con una cuenta configurada.
La sesión recordada requiere `AUTH_COOKIE_SECRET` (mínimo 32 caracteres) y almacenamiento
persistente de revocaciones. La restauración tras recrear la app está cubierta offline; la
persistencia real tras F5 en Cloud requiere validación autorizada.

---

## 📊 Fase 1: Transformación MASTERDATA

### Paso 1: Ir a Masterdata
1. En la barra lateral, selecciona **"📊 ABCD"**
2. Luego en el submenú: **"📥 Importador Masterdata"**

### Paso 2: Subir Excel Origen
1. **Sección 1**: "Cargar Excel origen Luxottica"
2. Click en "Sube el Excel origen"
3. **Opción A**: Subir archivo nuevo
   - Buscar: `docs/MasterData/Prueba grande.xlsx`
   - O cualquier otro Excel de Luxottica

4. **Opción B**: Usar existente
   - La app mostrará archivos disponibles

### Paso 3: Revisar Advertencias
- ⚠️ La app mostrará:
  - Productos procesados
  - Registros exportados
  - Incidencias detectadas
  - Pérdida de ceros iniciales

### Paso 4: Descargar Resultados
**Sección 2**: "Descargar resultados"
- 📥 **Excel transformado** (.xlsx)
- 📊 **JSON de validación** (.json)
- 📋 **Resumen ejecutivo** (.md)
- 🔍 **Auditorías CSV**:
  - Descartes
  - Ceros iniciales
  - Marcas no resueltas

### Paso 5: Obtener los IDs de canales de venta desde el EAN

Cada gafa se identifica por su EAN, guardado en `product_product.barcode`.
Para obtener el ID de producto que usa cada canal de venta, se busca primero la
variante de Odoo y después sus registros en `diagonal_product_website`.

```sql
SELECT
      pp.barcode AS ean,
      pp.id AS product_id_odoo,
      dpw.name AS canal_venta,
      dpw.product_website_id AS id_producto_canal,
      dpw.published AS publicado,
      dpw.published_website_date AS fecha_subida,
      dpw.updated_website_date AS fecha_actualizacion,
      dpw.archived_website_date AS fecha_baja
FROM product_product pp
JOIN diagonal_product_website dpw ON dpw.product_id = pp.id
WHERE pp.barcode = '8056262672563'
ORDER BY dpw.siteweb_id;
```

Resultado comprobado para el EAN `8056262672563` (`RAY BAN WAYFARER RB2140 129431`):

| Canal de venta | ID de producto en el canal |
| --- | --- |
| Cettire | `8056262672563` |
| Poizon | `8056262672563` |
| Deipe | `S0017840` |
| Farfetch | `31715787` |
| Miinto | `70eb4458-0cdb-48be-99fc-afc72667624a` |
| Prestashop | `8056262672563` |

`diagonal_product_website.id` es el identificador interno del registro de
relación en Odoo. Para operaciones en el canal debe usarse
`diagonal_product_website.product_website_id`.

---

## 🎁 Fase 2: Preparar solicitud de imágenes Luxottica

La app prepara lotes manuales; no inicia sesión ni envía solicitudes al portal.

### Paso 1: Generar Lotes
**Sección 3**: "Solicitud de imágenes (Luxoptica)"

1. Revisa la vista previa y confirma solo EAN válidos sin registros ni archivos de imagen.
2. EAN completos, parciales, con solicitud activa, archivos sin catalogar o cobertura no
   verificable no se proponen; resuelve primero los casos de revisión.
3. La vista previa necesita acceso de solo lectura al catálogo, al repositorio de imágenes,
   al almacén de solicitudes y al historial de lotes. Si una fuente no está disponible,
   la preparación se bloquea.
4. Confirma la selección. Los archivos TXT y su manifiesto se guardan localmente, con un
   máximo de 250 EAN por lote. No se envía nada al portal.

### Paso 2: Descargar Lotes
El lote se envía manualmente por una persona autorizada conforme al procedimiento vigente.
La marca de estado en la app registra la acción declarada, no la aceptación del portal ni la
entrega de imágenes. No reenvíes un lote con resultado remoto ambiguo.

## 📧 Fase 3: Recepción e importación

El monitor Graph es independiente de la preparación y el envío. El código o la existencia de
un contenedor no demuestra que esté activo ni que las descargas funcionen. Verifica runtime,
recibos, errores y permisos según la [guía de validación local Cloud](../CLOUD_LOCAL_VALIDATION.md).
No ejecutes Graph ni descargues correo real sin autorización específica.

Las imágenes canónicas se guardan por EAN en `IMAGE_REPOSITORY_ROOT/<EAN>`. Los aliases de
mercado y la publicación remota siguen sus flujos separados; no se activan al navegar por la
biblioteca ni al generar un lote.

---

## ✅ Checklist - Flujo Completo

### En la Aplicación:
- [ ] Login exitoso
- [ ] Ir a Masterdata
- [ ] Subir Excel origen
- [ ] Revisar advertencias de transformación
- [ ] Descargar Excel transformado + auditorías
- [ ] Revisar cobertura, solicitudes activas y estados de revisión
- [ ] Confirmar únicamente EAN ausentes y descargar el TXT/manifiesto

### Envío manual autorizado:
- [ ] Registrar autorización para el sistema, lote, efecto y límite
- [ ] Persona autorizada envía el archivo conforme al procedimiento aprobado
- [ ] Guardar evidencia del resultado fuera de Git

### Recepción:
- [ ] Verificar el estado del monitor y recibos, sin inferir éxito por un contenedor activo
- [ ] Autorizar por separado cualquier lectura de Graph o descarga real
- [ ] Validar/importar primero en almacenamiento aislado

---

## 🐛 Troubleshooting

### La app no carga
```powershell
# Verificar que venv está activado
& ".\.venv\Scripts\Activate.ps1"

# Verificar que requirements están instalados
pip install -r requirements.txt

# Iniciar app
streamlit run app_enhanced.py
```

### No puedo generar lotes
- Revisar que la transformación contiene EAN válidos.
- Confirmar acceso de solo lectura al catálogo y a los archivos.
- Confirmar acceso al almacén de solicitudes y al historial de lotes; no omitir un error de acceso.
- Revisar los estados completos, parciales, pendientes, desconocidos o sin catalogar.

### Recepción o descarga no verificada
- No ejecutar una prueba de Graph contra correo real sin autorización específica.
- Consultar la [guía de validación local Cloud](../CLOUD_LOCAL_VALIDATION.md).
- No interpretar un monitor iniciado ni una marca local como prueba de descarga.

---

## 📞 Soporte Rápido

No copies credenciales, correos, imágenes ni estado de producción a Git. La configuración de
Graph se comprueba por un procedimiento autorizado y separado del flujo de solicitud.

---

## 🎯 Resumen del Flujo

```
┌─────────────────────────────────────────────────┐
│  1. SUBIR EXCEL LUXOTTICA                       │
│     ↓                                           │
│  2. TRANSFORMAR A MASTERDATA                    │
│     ↓                                           │
│  3. REVISAR Y CONFIRMAR EAN AUSENTES            │
│     ↓                                           │
│  4. DESCARGAR ARCHIVO .TXT                      │
│     ↓                                           │
│  5. ENVIAR A LUXOPTICA (manual)                 │
│     ↓                                           │
│  6. VALIDAR RECEPCIÓN CON AUTORIZACIÓN          │
│                                                 │
└─────────────────────────────────────────────────┘
```

Los envíos y descargas reales requieren autorización y evidencia independiente. No hay un tiempo
de respuesta ni una frecuencia de monitor garantizados por esta guía.
