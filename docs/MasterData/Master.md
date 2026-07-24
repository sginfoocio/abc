# Prompt Operativo Masterdata Luxottica -> Odoo

## Rol
Actúa como especialista en transformación de catálogos ópticos para Odoo.

## Objetivo
Transformar el Excel original de Luxottica a un Excel MASTERDATA limpio, normalizado y listo para importar en Odoo.

No limitarse a renombrar columnas: interpretar, limpiar, enriquecer y normalizar.

## Entradas
- Archivo Excel de Luxottica.
- Diccionarios desde BD:
  - diagonal_product_color
  - diagonal_product_color_dictionary
  - diagonal_product_material
  - diagonal_product_forma

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
- No perder ceros iniciales.
- Mantener letras finales.
- Conservar espacios (incluido Prada y Miu Miu).

## Reglas de color
- Mantener formato texto.
- No convertir color a numérico.
- Conservar ceros iniciales.
- Normalizar colores usando diccionario BD.
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
Mapear a:
- Rectangular
- Cuadrada
- Ovalada
- Pantos
- Aviador
- Ojo de Gato
- Mariposa
- Irregular

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
- Se toma de la columna H del archivo origen.

## Duplicados
- Dedupe por UPC.

## Salida esperada (24 columnas)
- Punto de venta
- Código del modelo
- Calibre
- Color
- UPC
- Nombre de la marca
- Código de marca
- Colección
- Género
- Forma
- Tipo
- Nombre del modelo
- Descripción del color
- Material del frente
- Color del frontal
- Material de las lentes
- Color de las lentes
- Fotocromático
- Polarizado
- Largo de varilla
- Dimensión del puente
- PVP sugerido
- PVO
- Categoría

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
