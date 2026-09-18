# Prompt Operativo Masterdata Luxottica -> Odoo

## Rol
Actúa como especialista en transformación de catálogos ópticos para Odoo.

## Objetivo
Transformar el Excel original de Luxottica a un Excel MASTERDATA limpio, normalizado y listo para importar en Odoo.

No limitarse a renombrar columnas: interpretar, limpiar, enriquecer y normalizar.

Este documento refleja el comportamiento actualmente implementado en la app y en el script de transformación.

## Usuarios y permisos

La aplicación admite dos perfiles:

- **Administrador**: credenciales `APP_USERNAME` y `APP_PASSWORD`; acceso a
  todas las áreas.
- **Master Data**: credenciales `MASTERDATA_USERNAME` y
  `MASTERDATA_PASSWORD`; acceso únicamente a Importador Masterdata,
  Diccionario y Dry-run Odoo.

En Docker, las cuatro variables deben estar definidas en el `.env` del host.
No se guardan contraseñas en el repositorio:

```env
APP_USERNAME=usuario_administrador
APP_PASSWORD=********
MASTERDATA_USERNAME=usuario_masterdata
MASTERDATA_PASSWORD=********
```

El usuario limitado no verá en el menú Análisis ABC, Alertas ni Configuración.
Si intenta acceder directamente a una página restringida, la aplicación lo
bloquea igualmente.

## Entradas
- Archivo Excel de Luxottica.
- Diccionario personalizado editable desde la sección **Master Data > Diccionario**.
- Diccionarios desde BD:
  - diagonal_product_color
  - diagonal_product_color_dictionary
  - diagonal_product_material
  - diagonal_product_forma

## Diccionario personalizado

La aplicación incluye un apartado **Diccionario** para crear reglas de
transformación específicas del negocio. Cada regla tiene exactamente estas
columnas:

| Columna | Valor | Transformado |
|---|---|---|
| Color | BLACK | Negro |
| Forma | Cat Eye | Cat Eye |
| Género | KIDS | Niño |

El campo **Columna** se selecciona mediante un desplegable con el esquema fijo
de cabeceras del Excel original, antes de transformar el fichero. No es
necesario cargar un Excel adicional en este apartado y así se evitan errores de
escritura o referencias a nombres de columnas transformadas.

Las reglas se guardan en:

`docs/MasterData/masterdata_dictionary.json`

El valor se compara de forma exacta ignorando mayúsculas y espacios exteriores.
Las reglas personalizadas se aplican antes de los diccionarios de Odoo, por lo
que tienen prioridad para resolver excepciones del negocio. Después se aplican
las normalizaciones estándar de color, forma, género y demás campos.

Se han incorporado inicialmente las relaciones detectadas en la revisión:

- `Categoría`: `Gafas de vista` -> `MONTURAS`.
- `Color de las lentes`: `multiples` -> vacío.
- `Forma`: Aviador -> `AVIATOR`; Cuadrada, Irregular, Pillow, Rectangular y
  Square -> `RECTANGULAR/CUADRADA`; Mariposa y Ojo de gato -> `CAT EYE`;
  Ovalada, Pantos y Redonda -> `REDONDA/OVALADA`.
- `Material del frente`: `Acero` -> `METAL`.
- `PVP sugerido`: `Todo vacio` -> vacío.

Si una regla guardada no aparece en el XLS cargado, la aplicación muestra una
alerta con las reglas no encontradas. Esto permite detectar valores obsoletos,
errores de escritura o cambios en el fichero de Luxottica.

Además, la aplicación revisa los valores presentes en el XLS de las columnas
`Color`, `Descripción del color`, `Color del frontal`, `Color de las lentes` y
`Forma`. Si un valor no tiene relación ni con los diccionarios de Odoo ni con
una regla personalizada, se muestra una alerta con la columna, el valor y el
número de filas afectadas. Estos valores no se descartan automáticamente: el
usuario puede añadir la relación desde el apartado **Diccionario** y volver a
transformar el fichero.

El diccionario no modifica el Excel original. Sus transformaciones solo afectan
al resultado de la ejecución actual y a las descargas generadas.

## Reglas de depuración
- Eliminar accesorios.
- Eliminar marca Ralph Lauren.
- Mantener Polo Ralph Lauren.
- Mantener solo productos válidos para importación.
- No eliminar filas válidas.

## Reglas de marca
- Unificar:
  - Dolce & Gabbana
  - Dolce e Gabbana
  - Dolce y Gabbana
  - a: Dolce Gabbana
- Ray Ban siempre sin guion.
- Distinguir:
  - Ray Ban
  - Ray Ban Junior
  - Ray Ban Meta
- Ray Ban Junior:
  - Junior Sol -> RJ
  - Junior Vista -> RB
  - Género -> Niño
- Reemplazar RX y RY por RB (código de marca y código/modelo cuando aplique).
- Oakley: normalizar códigos OO/OJ/OY/OX.

## Reglas de modelo
- Eliminar "/" y "-".
- Concatenar resultado.
- Mantener formato texto.
- Extraer un "0" inicial cuando venga como prefijo del modelo.
- Mantener letras finales.
- Conservar espacios (incluido Prada y Miu Miu).

## Reglas de color
- Mantener formato texto.
- No convertir color a numérico.
- Conservar ceros iniciales.
- Consultar primero el diccionario `diagonal_product_color_dictionary` unido a
  `diagonal_product_color`.
- Incluir también los nombres base de `diagonal_product_color` como alias.
- Resolver tanto valores alfabéticos como alfanuméricos antes de aplicar el
  fallback.
- Ejemplos: `BLACK` -> `Negro`, `GREEN` -> `Verde`.
- Unificar colores español/inglés.
- Regla especial: NEGRO/TALCO -> BLANCO.

## Reglas de lentes
- Monturas:
  - Vaciar Color de las lentes.
  - Vaciar Material de las lentes cuando corresponda.
- Gafas de sol:
  - Mantener Color de las lentes.
  - Normalizar Color de las lentes.

## Reglas de género
Mapear solo a:
- Hombre
- Mujer
- Unisex
- Niño

Todo producto infantil debe quedar en Niño.

## Reglas fotocromático/polarizado
- Valores permitidos: SI o NO.
- No se permiten vacíos.

## Reglas de categoría
Mapear solo a:
- Gafas de vista
- Gafas de sol

## Reglas de forma
- Consultar el catálogo `diagonal_product_forma` de Odoo.
- Normalizar el valor de entrada y buscarlo en el diccionario de formas.
- Conservar el nombre canónico definido en Odoo.
- Ejemplos: `Aviator` -> `Aviator`, `Cat Eye` -> `Cat Eye`.
- Si no existe correspondencia, aplicar el mapa de respaldo y usar `Irregular`
  cuando no sea posible determinar la forma.

Formas canónicas actualmente disponibles en Odoo:
- Aviator
- Cat Eye
- Oversize
- Rectangular/Cuadrada
- Redonda/Ovalada
- Butterfly
- Geométrica
- Piloto
- Pantos
- Rectangular
- Cuadrada
- Ovalada
- Redonda
- Visor
- Shield
- Máscara

## Reglas de materiales
Mapear a:
- Acetato
- Metal
- Acero
- Acetato y Metal
- Titanio
- Nylon
- Biopoliamida

## Columna de colección
- Se toma de la columna L (`Nombre del modelo`) del archivo origen.

## Regla de PVP
- Priorizar `PVP sugerido`.
- Si el origen trae `PVP` (columna W) en lugar de `PVP sugerido`, usar `PVP` como fuente.

## Duplicados
- Dedupe por UPC.

## Salida técnica transformada (18 columnas)
Orden exacto:
- Marca
- Colección
- Modelo
- Color
- Calibre
- Ancho Puente
- Longitud Varilla
- Género
- Color Frontal
- Color Lente
- Forma
- Material Principal
- Fotocromático
- Polarizado
- PVO
- PVP
- Barcode
- Categoría

## Descarga XLS/CSV en la web
- Las descargas del transformado conservan las 18 columnas.
- El CSV usa `;` como separador, igual que el fichero MASTERDATA de referencia.
- Orden exacto del XLSX y CSV descargado:
  - Barcode
  - Categoría
  - Marca
  - Colección
  - Modelo
  - Color
  - Calibre
  - Ancho Puente
  - Longitud Varilla
  - Género
  - Color Frontal
  - Color Lente
  - Forma
  - Material Principal
  - Fotocromático
  - Polarizado
  - PVO
  - PVP

## Validaciones obligatorias
- Verificar que no se pierdan registros válidos.
- Verificar integridad UPC.
- Verificar ceros iniciales.
- Verificar Fotocromático y Polarizado con SI/NO.
- Detectar valores anómalos.

## Entrega final
1. Excel transformado.
2. Resumen de validación.
3. Número de registros procesados.
4. Número de registros descartados.
5. Incidencias detectadas.
