# Rutas y correcciones: 11 EAN × 14 competidores

Investigación del 22/11/2026. Paquete de descubrimiento para el trabajo en curso de #145; MySQL queda separado.

## Resultado

154 pares únicos: 94 con ficha o página de variantes localizada en índice/texto, 12 solo con candidata documental, 2 solo con pista de catálogo, 1 ruta redirigida a catálogo y 45 sin ruta localizada. **No son 94 ofertas actuales ni 94 coincidencias exactas de EAN.** Hay modelos/colores, otros mercados, evidencia antigua y selectores pendientes. No se alteró producción ni se crearon solicitudes del monitor.

| EAN | Modelo | Ficha/variantes localizada | Solo documental | Solo catálogo | Redirección inválida | Sin ruta |
|---|---|---:|---:|---:|---:|---:|
| `0843023181451` | Cartier CT0550S 001 | 4 | 0 | 0 | 0 | 10 |
| `8056597886932` | Miu Miu MU 52YS 7OE01T | 10 | 2 | 0 | 0 | 2 |
| `8056262407691` | Miu Miu MU A06S 14L09Z | 10 | 1 | 0 | 1 | 2 |
| `8056597875349` | Prada PR 27ZS 16K08Z | 10 | 3 | 1 | 0 | 0 |
| `0805289126591` | Ray-Ban RB2140 901/58 | 11 | 0 | 1 | 0 | 2 |
| `0889652429687` | Saint Laurent SL 596 DUNE 003 | 9 | 0 | 0 | 0 | 5 |
| `0889214625984` | Tom Ford FT1310 01A | 9 | 2 | 0 | 0 | 3 |
| `0889214669742` | Tom Ford FT1360 30A | 8 | 0 | 0 | 0 | 6 |
| `0889214042422` | Tom Ford FT5634-B 001 | 7 | 3 | 0 | 0 | 4 |
| `0889214586650` | Tom Ford FT6058-B 001 | 7 | 1 | 0 | 0 | 6 |
| `0889214599834` | Tom Ford FT6082-B 051 | 9 | 0 | 0 | 0 | 5 |

## Cómo interpretar la evidencia

- Los precios y el stock de caché conservan su fecha; no se sustituyen por la fecha de esta investigación.
- Sin resultado del buscador no demuestra que la tienda no venda la gafa. No se auditó exhaustivamente cada catálogo.
- Una URL extranjera solo orienta el descubrimiento. No autoriza a copiar su precio/stock al mercado español.
- La deduplicación real debe comprobarse contra muestras, aprobaciones, cola, leases y recibos vivos; este paquete no tiene ese estado actual.
- El calibre no cambia el precio del mismo modelo/color. Esto no hace equivalentes códigos distintos ni permite trasladar stock.

## Correcciones por competidor

### Óptica Óptima

Resolver nombres comerciales en rutas: FT1310 usa tom-ford-chloe-02-ft1310-01a.html y FT1360 usa tom-ford-liam-02-ft1360-30a.html. Para RB2140 901/58, la ficha correcta es ray-ban-rb2140-901-58-original-wayfarer.html; la ficha 901 contiene un enlace relacionado, no la oferta buscada. El texto de FT5634-B 001 devuelve importes/descuentos inconsistentes: conservar conflicto y extraer solo la oferta vinculada al contexto ES/EUR/variante.

### Amevista

Usar /es/ y rutas observadas: miu-miu-0mu-52ys-7oe01t, miu-miu-mu-a06s-14l09z, saint-laurent-sl-596-dune-003, ray-ban-rb2140-901-58-wayfarer y tom-ford-ft6058-b-001. Separar modelo de nombre comercial; preservar B y A06S. La ficha RB2140 lista varios EAN por calibre: el precio común del modelo/color puede compartirse si está acreditado; no asignar stock de otra talla. No sumar descuentos condicionados a lentes.

### Visio Factory

Descubrir desde catálogo los slugs con espacios internos, puentes y nombres. FT5634-B/V 001 publica 889214042422 y FT6058-B/V 001 publica 889214586650: permitir estos alias acotados al competidor y evidencia, conservando B. Los dos códigos normalizados coinciden con los EAN solicitados. Leer stock del producto principal, no de otros colores.

### Edel-Optics

MU-A06S-14L09Z-de-Miu-Miu.html redirige a Gafas-de-sol-Miu-Miu.html en la respuesta recuperada: rechazar como ficha, descubrir enlace verdadero. MU52YS quedó documental sin respuesta recuperable. RB2140 901/58 tiene URL con WAYFARER-(RB2140-901-58)-de-Ray-Ban.html, no una plantilla genérica. selectedSize 516/517 aparecen en índice con varios EAN; vincular selección real a Product/Offer/radio y no inferir cuál corresponde sin evidencia. FT6058-B 001 publica Product Id 2146764 y 889214586650. Distinguir entrega 1–2/2–3 semanas de agotado, conservando la política comercial vigente.

### Domanci

MU A06S 14L09Z tiene ficha ES, EAN 8056262407691 y referencia 0MU_A06S14L09Z5417. Separar prefijo comercial 0MU, modelo, color y medidas con reglas acotadas, sin cortar a ciegas el final del color. SL596 DUNE 003 tiene ruta de sol; no mezclar DUNE OPT. Las rutas previas de Prada/FT5634 aún son candidatas documentales: comprobar evidencia existente antes de consultar.

### ConGafasDeSol

Prada y FT5634 tienen fichas indexadas. RB2140 901/58 aparece en catálogo, pero falta enlace exacto. MU52YS y FT1310 conservan rutas documentales sin resultado actual suficiente: no presentar slugs generados como fichas existentes. Si hay antibot o bloqueo, registrar y respetar; no reintentar mediante otro idioma.

### Óptica Bassol

Conservar slugs observados, incluido miu-miu-0mu-52ys frente a miu-miu-mu-a06s. FT6058-B 001 tiene ruta raíz española indexada. SL596 DUNE 003 tiene página raíz que muestra España/EUR. Otras rutas se localizaron en países distintos: descubrir/validar destino España sin copiar precio ni stock entre mercados. Manufacturing/fabricación no equivale a stock inmediato.

### Blickers

Conservar el identificador numérico de producto; C53/C54/C58 describe el calibre en el título y no es parte del modelo. Nuevas alternativas para FT5634-B, FT6058-B, FT1310, FT1360 y SL596 DUNE. SL596 de sol ID 5837987065692160; DUNE OPT ID 5010623020728320 es otro producto. Algunas rutas solo se encontraron en otros mercados: resolver España sin inventar precio.

### Visual-Click

Usar slugs observados con nombres comerciales cuando existan (chloe-02, liam-02). Preservar FT6058-B/FT6082-B completos. MU52YS aparece agotado en evidencia antigua: no aceptar por botón genérico o disponibilidad de otra ficha. Excluir productos relacionados y variantes de otro color. El prefijo /en/ no acredita ni descarta por sí solo país ES: comprobar contexto.

### Óptica Universitaria

Conservar IDs de ficha: Prada 312245; RB2140 901/58 calibre50 160244 y calibre54 176253. El calibre no cambia el precio del mismo modelo/color, pero cada variante conserva identidad/stock. No convertir coincidencias de categorías o de otro modelo/color Miu Miu en hallazgos para estos EAN.

### Optical H

Reutilizar datos y recibos existentes; esta investigación solo añade pistas indexadas. Respetar el 429/bloqueo ya registrado en producción: no forzar capturas ni abrir otro país para sortearlo. Separar país/currency de idioma de URL; enlazar GTIN/Offer y disponibilidad individual.

### Mia Burton

MU52YS tiene ficha española y EAN exacto; los campos BARCODE internos no son automáticamente GTIN del fabricante. Cartier publica UPC objetivo y otros identificadores distintos: no declarar todos equivalentes ni ignorar una contradicción. Precio común no acredita stock de cada talla. Prada solo está acreditado aquí por catálogo extranjero; extraer enlace real. No considerar un fallo de caché del proveedor como 404 del comercio.

### Otticait

Resolver rutas /es/ conservando nombres comerciales y guion B. Varias fichas solo tienen evidencia extranjera o documental, por lo que falta país ES/oferta actual. No inferir mercado por idioma ni usar ese cambio para sortear bloqueos.

### Céline Opticien Lunetier

Los /products/ agrupan varios colores. El resultado de búsqueda cita el color solicitado, pero falta vincular variante–EAN–precio–stock y contexto España. Cartier conserva una URL antigua extranjera; resolver destino y migración sin convertir esa ficha histórica en oferta vigente.

## Rutas por EAN y competidor

Se muestra una URL prioritaria por par. El JSON conserva las alternativas y la procedencia. Las rutas documentales o redirigidas se excluyen de los 94 hallazgos de ficha/variantes.

### Cartier CT0550S 001 — 0843023181451

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Amevista | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/cartier-core-range-ct0550s-001-56-18-oro.html) |
| Edel-Optics | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en-gb/products/cartier-ct0550s-001-56) |
| Blickers | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Visual-Click | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/en/sunglasses/cartier/ct0550s-001) |
| Otticait | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/ru/solncezasitnye-ocki-cartier/164577-solncezasshitnye-ochki-cartier-core-range-ct0550s.html) |

### Miu Miu MU 52YS 7OE01T — 8056597886932

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/miu-miu-mu-52ys-7oe01t.html?SubmitCurrency=1&id_currency=22) |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/miu-miu-0mu-52ys-7oe01t) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/mu-52ys-7oe-01t-54-21.html) |
| Edel-Optics | solo_candidata_documental | [Abrir](https://www.edel-optics.es/MU-52YS-7OE01T-de-Miu-Miu.html) |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | solo_candidata_documental | [Abrir](https://www.congafasdesol.com/miu-miu-mu-52ys-7oe01t.html) |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en-eu/products/miu-miu-0mu-52ys-7oe01t-54) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/es-es/gafas/miu-miu-mu-52ys-c54/5075722152574976/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/miu-miu-mu-52ys-7oe01t-54) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | ficha_o_variante_localizada | [Abrir](https://opticalh.com/en-eu/products/miu-miu-52ys-7oe01t-54-21-sunglasses-63700) |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/es/gafas-de-sol/miu-miu/mu-52ys-7oe01t) |
| Otticait | ficha_o_variante_localizada | [Abrir](https://www.otticait.com/en/catalog/product/view/id/113064/s/miu-miu-mu-52ys-7oe01t/category/6/) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-miu-miu-logo-mu-52ys) |

### Miu Miu MU A06S 14L09Z — 8056262407691

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/miu-miu-mu-a06s-14l09z.html?SubmitCurrency=1&id_currency=1) |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/miu-miu-mu-a06s-14l09z) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/miu-miu-mu-a06s-14l-09z-54-17-havana-honey.html) |
| Edel-Optics | redireccion_a_catalogo | [Abrir](https://www.edel-optics.es/MU-A06S-14L09Z-de-Miu-Miu.html) |
| Domanci | ficha_o_variante_localizada | [Abrir](https://domanci.com/es/gafas-de-sol/miu-miu-mu-a06s-14l09z-54.html) |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en/products/miu-miu-mu-a06s-14l09z-54) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/es-es/gafas/miu-miu-mu-a06s-c54/6414860414746624/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/miu-miu-mu-a06s-14l09z-54) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | ficha_o_variante_localizada | [Abrir](https://opticalh.com/en-us/products/miu-miu-0mu-a06s-14l09z-54-17-64155) |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/es/gafas-de-sol/miu-miu/mu-a06s-14l09z) |
| Otticait | solo_candidata_documental | [Abrir](https://www.otticait.com/es/miu-miu-mu-a06s-14l09z) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-miu-miu-mu-a06s/) |

### Prada PR 27ZS 16K08Z — 8056597875349

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/prada-pr-27zs-16k08z.html?SubmitCurrency=1&id_currency=38) |
| Amevista | solo_candidata_documental | [Abrir](https://www.amevista.com/es/prada-0pr-27zs-16k08z) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/pr27zs-16k08z-54-21.html) |
| Edel-Optics | solo_candidata_documental | [Abrir](https://www.edel-optics.es/PR-27ZS-16K08Z-de-Prada.html) |
| Domanci | solo_candidata_documental | [Abrir](https://domanci.com/es/gafas-de-sol/prada-pr-27zs-16k08z-54.html) |
| ConGafasDeSol | ficha_o_variante_localizada | [Abrir](https://www.congafasdesol.com/prada-pr-27zs-16k08z.html) |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en-eu/products/prada-pr-27zs-16k08z-54) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/nl-be/brillen/prada-pr-27zs-c54/5649966192263168/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/prada-pr-27zs-16k08z-54) |
| Óptica Universitaria | ficha_o_variante_localizada | [Abrir](https://www.opticauniversitaria.es/gafas-de-sol/prada/prada-pr-27zs-16k08z/312245.html) |
| Optical H | ficha_o_variante_localizada | [Abrir](https://opticalh.com/en/products/prada-pr-27zs-16k08z-54-21-sunglasses-62083) |
| Mia Burton | solo_pista_catalogo | [Abrir](https://miaburton.com/de/sonnenbrillen/rechteckige/2?vmEnabled=true) |
| Otticait | ficha_o_variante_localizada | [Abrir](https://www.otticait.com/en/prada-pr-27zs-16k08z) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-prada-symbole-pr-27zs) |

### Ray-Ban RB2140 901/58 — 0805289126591

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/ray-ban-rb2140-901-58-original-wayfarer.html) |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/ray-ban-rb2140-901-58-wayfarer) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-de-sol/todas-las-solares/rb2140-901-58-50-22.html) |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/WAYFARER-%28RB2140-901-58%29-de-Ray-Ban.html) |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | solo_pista_catalogo | [Abrir](https://www.congafasdesol.com/gafas-de-sol/marcas/ray-ban/hombre.html) |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/sv-se/products/ray-ban-rb2140-wayfarer-901-58-54) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/de-de/brillen/ray-ban-wayfarer-rb2140-c54/5499054280671232/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/products/ray-ban-rb2140-wayfarer-901-58-54) |
| Óptica Universitaria | ficha_o_variante_localizada | [Abrir](https://www.opticauniversitaria.es/gafas-de-sol/ray-ban/wayfarer/ray-ban-wayfarer-rb-2140-50-901-58/160244.html) |
| Optical H | ficha_o_variante_localizada | [Abrir](https://opticalh.com/en-eu/products/rayban-wayfarer-2140-90158-50-22-1184) |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/en/sunglasses/ray-ban/wayfarer-rb2140-901-58) |
| Otticait | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-ray-ban-rb2140?SubmitCurrency=1&id_currency=5) |

### Saint Laurent SL 596 DUNE 003 — 0889652429687

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/saint-laurent-sl-596-dune-003.html) |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/saint-laurent-sl-596-dune-003) |
| Visio Factory | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/SL-596-DUNE-003-de-Saint-Laurent.html) |
| Domanci | ficha_o_variante_localizada | [Abrir](https://domanci.com/es/gafas-de-sol/saint-laurent-sl-596-dune-003-58.html) |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/products/saint-laurent-sl-596-dune-003-58) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/es-es/gafas/saint-laurent-sl-596-dune-c58/5837987065692160/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/saint-laurent-sl-596-dune-003-58) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/en/sunglasses/saint-laurent/dune-sl-596-003) |
| Otticait | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-saint-laurent-corner-angle-sl-596-dune) |

### Tom Ford FT1310 01A — 0889214625984

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/tom-ford-chloe-02-ft1310-01a.html) |
| Amevista | solo_candidata_documental | [Abrir](https://www.amevista.com/es/tom-ford-ft1310-01a) |
| Visio Factory | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/FT1310-01A-de-Tom-Ford.html) |
| Domanci | ficha_o_variante_localizada | [Abrir](https://domanci.com/en/sunglasses/tom-ford-chloe-02-ft1310-01a-52.html) |
| ConGafasDeSol | solo_candidata_documental | [Abrir](https://www.congafasdesol.com/tom-ford-ft1310-01a.html) |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en-gb/products/tom-ford-ft1310-01a-52) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/en-gb/glasses/tom-ford-ft1310-chloe-02-c52/4836809833840640/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en-int/products/tom-ford-ft1310-chloe-02-01a-52) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/es/gafas-de-sol/tom-ford/ft1310-01a) |
| Otticait | ficha_o_variante_localizada | [Abrir](https://www.otticait.com/en/tom-ford-chloe-02-ft1310-01a) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/nl/products/zonnebrillen-tom-ford-chloe-02-ft1310) |

### Tom Ford FT1360 30A — 0889214669742

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-de-sol/tom-ford-liam-02-ft1360-30a.html) |
| Amevista | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Visio Factory | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/FT1360-30A-de-Tom-Ford.html) |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en/products/tom-ford-ft1360-30a-51) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/en-gb/glasses/tom-ford-ft1360-liam-02-c51/5792072117321728/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en-au/products/tom-ford-ft1360-liam-02-30a-51) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/en/sunglasses/tom-ford/liam-02-ft1360-30a) |
| Otticait | ficha_o_variante_localizada | [Abrir](https://www.otticait.com/en/tom-ford-liam-02-ft1360-30a) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/sunglasses-tom-ford-liam-02-ft1360) |

### Tom Ford FT5634-B 001 — 0889214042422

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | ficha_o_variante_localizada | [Abrir](https://optica-optima.com/gafas-graduadas/tom-ford-ft5634-b-001.html) |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/tom-ford-ft5634-b-001) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/ft5634-b-v-001-53-20.html) |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/FT5634-B-001-de-Tom-Ford.html) |
| Domanci | solo_candidata_documental | [Abrir](https://domanci.com/es/monturas/tom-ford-ft5634-b-001-53.html) |
| ConGafasDeSol | ficha_o_variante_localizada | [Abrir](https://www.congafasdesol.com/tom-ford-ft5634-b-001.html) |
| Óptica Bassol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/es-es/gafas/tom-ford-ft5634-b-c53/6462211885629440/) |
| Visual-Click | solo_candidata_documental | [Abrir](https://visual-click.com/products/tom-ford-ft5634-b-001-53) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Otticait | solo_candidata_documental | [Abrir](https://www.otticait.com/tom-ford-ft5634-b-001) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/prescription-glasses-tom-ford-ft5634-b) |

### Tom Ford FT6058-B 001 — 0889214586650

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/es/tom-ford-ft6058-b-001) |
| Visio Factory | ficha_o_variante_localizada | [Abrir](https://www.visiofactory.es/gafas-graduadas/todas-las-opticas/tom-ford-ft6058-b-v-001-58-16-negro.html) |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/FT6058-B-001-de-Tom-Ford.html) |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/products/tom-ford-ft6058-b-001-58) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/es-es/gafas/tom-ford-ft6058-b-c58/6268355773071360/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/tom-ford-ft6058-b-001-58) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/en/eyeglasses/tom-ford/ft6058-b-001) |
| Otticait | solo_candidata_documental | [Abrir](https://www.otticait.com/fr/tom-ford-ft6058-b-001) |
| Céline Opticien Lunetier | sin_ruta_localizada_en_indice | Sin ruta localizada |

### Tom Ford FT6082-B 051 — 0889214599834

| Competidor | Evidencia | Ruta prioritaria |
|---|---|---|
| Óptica Óptima | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Amevista | ficha_o_variante_localizada | [Abrir](https://www.amevista.com/us/tom-ford-ft6082-b-051) |
| Visio Factory | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Edel-Optics | ficha_o_variante_localizada | [Abrir](https://www.edel-optics.es/FT6082-B-051-de-Tom-Ford.html) |
| Domanci | sin_ruta_localizada_en_indice | Sin ruta localizada |
| ConGafasDeSol | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Óptica Bassol | ficha_o_variante_localizada | [Abrir](https://www.opticabassol.com/en/products/tom-ford-ft6082-b-051-57) |
| Blickers | ficha_o_variante_localizada | [Abrir](https://www.blickers.com/nl-be/brillen/tom-ford-ft6082-b-c57/5654711419928576/) |
| Visual-Click | ficha_o_variante_localizada | [Abrir](https://visual-click.com/en/products/tom-ford-ft6082-b-051-57) |
| Óptica Universitaria | sin_ruta_localizada_en_indice | Sin ruta localizada |
| Optical H | ficha_o_variante_localizada | [Abrir](https://opticalh.com/en-eu/products/en-optical-frames-tom-ford-ft6082-b-051-57-13) |
| Mia Burton | ficha_o_variante_localizada | [Abrir](https://miaburton.com/es/gafas-graduadas/tom-ford/ft6082-b-051) |
| Otticait | ficha_o_variante_localizada | [Abrir](https://www.otticait.com/es/tom-ford-ft6082-b-051) |
| Céline Opticien Lunetier | ficha_o_variante_localizada | [Abrir](https://www.celine-opticien-lunetier.com/products/eyeglasses-tom-ford-ft6082-b) |


