# Patrones consolidados de Domanci y ConGafasDeSol

Continuación conjunta de #118 sobre `c6e2be7ba8d183187f14f3c02bad30e4cd866511`.
La auditoría anterior ya recogía el patrón de Domanci como hipótesis y las excepciones
de nombres comerciales de ConGafasDeSol. Esta ampliación incorpora los ejemplos y
la regla general aportados por el usuario el 6 de noviembre; no es una nueva auditoría.

## Reglas incorporadas en el mismo cambio

| Tienda | Construcción de candidatas | Datos necesarios |
|---|---|---|
| Domanci | `/es/{gafas-de-sol|monturas}/{marca}-{modelo}-{color}-{calibre}.html` | Marca, modelo, color, categoría y calibre del registro del EAN objetivo |
| ConGafasDeSol | `/{marca}-{modelo}-{color}.html`, para sol | Marca, modelo, color y categoría; no exige calibre |

Ejemplo aportado de Domanci:
`https://domanci.com/es/gafas-de-sol/miu-miu-mu-11zs-16k5s0-51.html`.
El `51` solo describe ese ejemplo: nunca se utiliza como medida por defecto. Un calibre
registrado como `54-21` aporta `54` a la URL del EAN correspondiente; el puente no se
necesita. La categoría `monturas` ya aparece en E033. Es una extensión de la hipótesis
de ruta, no una confirmación HTTP de todas las marcas o categorías.

Ejemplos aportados de ConGafasDeSol:
- `https://www.congafasdesol.com/prada-pr-d01s-0cd80l.html`
- `https://www.congafasdesol.com/prada-pr06ys-09q5s0.html`

Las referencias equivalentes de Vytria facilitadas por el usuario usan
`/es/prada-pr-d01s-0cd80l.html` y `/es/prada-pr-06ys-09q5s0.html`.
Son ejemplos de normalización de referencia; no son hechos extraídos de las tiendas.
Para Prada se proponen como máximo dos grafías deterministas del prefijo `PR`:
compacta y separada. Se prueban primero las formas observadas por familia (numérica
compacta, alfanumérica separada). No se enumeran colores, medidas, IDs ni nombres.
Las URLs documentadas con nombres comerciales, como Tom Ford Fausto (E037/E038),
conservan prioridad y su forma exacta.

La procedencia identifica los ejemplos aportados y fecha `2026-11-06`, separada de
las 47 evidencias originales del `2026-11-04`. Ninguna URL nueva se presenta como
una ficha verificada ni como autorización de acceso.

## Cobertura local y límites de los datos

Simulación offline sobre la copia de `Productos_100.csv` de la auditoría disponible
en los archivos de esta conversación (no en el inventario actual de Cloud):
SHA-256 `63ee2a6a5b1932d4b9bb89da941edc82d5ce299579e3410ce1fc7623c66d4d5a`.
La copia documental no está incluida en este checkout del repositorio.

| Tienda | EAN con candidata ejecutable por atributos | URLs propuestas | Pendientes |
|---|---:|---:|---|
| ConGafasDeSol | 89 | 102 | 11 monturas fuera del patrón de sol |
| Domanci | 0 | 0 | Los 100 registros de esta copia histórica carecen de calibre; dos conservan candidatas históricas pendientes y 98 informan calibre ausente |
| Amevista, heredado de #118 | 49 | 49 | 51 fuera del alcance documentado; sin cambios en esta PR |

«Ejecutable por atributos» solo describe el generador. ConGafasDeSol tenía un bloqueo
CAPTCHA y falta de permiso registrados en el contexto del piloto; no se han eliminado
ni revalidado desde esta rama. Por tanto, 89 candidatas no equivalen a 89 pares
autorizados, encontrados o comparables.

El usuario indica que el calibre está disponible. Si está asociado a los EAN en los
registros actuales de Cloud, Domanci ya lo aprovecha sin necesitar más reglas por
producto. Esta simulación no permite verificar ese enriquecimiento actual ni copiar
medidas de otro EAN. No se modifica el catálogo para rellenar calibres.

No se amplían reglas de IDs opacos (Edel/Blickers), medidas faltantes ni handles con
nombres comerciales no documentados de otras tiendas. Esos requisitos no se pueden
resolver eliminando las comprobaciones de identidad. No se abren cambios por cada EAN.

## Validación y ejecución

Una sola comprobación local focalizada: 93 pruebas aprobadas entre
`test_price_supplied_patterns.py` y `test_price_url_candidates.py`.
Incluye atributos variables, ausencia de calibre, prefijos compactos/separados,
reutilización de nombres documentados, preview sin HTTP, localización y monitorización,
rechazo de color contradictorio y frenos por permiso, bloqueo, robots y presupuesto.
Todo el transporte es sintético. El lint de errores y `git diff --check` también pasan.
La CI completa del repositorio se ejecutará una vez para esta PR consolidada.

No hay migraciones, cambios de permisos, borrado de bloqueos, sincronización Vytria,
alteración de revisiones/históricos, consultas comerciales, recurrencia ni despliegue.
La ejecución posterior utiliza el lote supervisado existente y las condiciones reales
de acceso y cola; generar una candidata no la añade como ficha verificada ni oferta.
