# Sincronización del PVP Vytria desde Odoo

## Precio final público de PrestaShop (preparación, deshabilitada por defecto)

La tarifa 59 sigue siendo la referencia efectiva de Odoo. PrestaShop puede aplicar
ofertas posteriores a esa tarifa: son diferencias comerciales, no errores de Odoo.
Cuando se habilita el lector, solo para la intersección del manifiesto existente de
100 EAN con los vigilados activos y autorizados por la ejecución, se compara el
resultado vigente del cálculo MySQL. Una lectura fallida o caducada conserva el
último resultado y su fecha real, pero no se compara ni se sustituye por Odoo.
Sin habilitación y sin lectura anterior, la operativa existente no cambia.
Administración y ficha muestran por separado el Precio Odoo, el Precio final
Vytria, la oferta/descuento aplicado, el origen y la fecha de actualización.
La ficha marca además el estado de vigencia; un precio anterior puede quedar
visible como referencia histórica, pero no se usa en la comparación.

**Contraste público (2026-11-20):** un GET sin sesión autenticada a la
[ficha Prada PR 17WS 2AU8C1](https://www.vytria.com/es/pr-17ws-2au8c1.html)
mostró EAN `8056597435581`, calibre 49, país `ES` (6), moneda `EUR` (1),
tienda `Vytria` (1) y `is_logged=false`. La ficha publicó **232,05 €**,
también en `product:price:amount`, y mostró **273,00 €** como precio anterior.
El producto público indicado es `498`; el `68` del selector es un valor de
atributo, no el ID de la combinación.
El importe público no se usa como semilla, ni esta URL se usa como lector automático.

**Contraste MySQL (2026-11-20):** MariaDB 11.8.10, esquema de PrestaShop
8.2.1 (`PS_VERSION_DB`), prefijo `ps_`, una tienda activa (Vytria, ID 1).
El grupo anónimo es **Visitante (1)**, no Invitado (2); muestra IVA y no tiene
reducción de grupo. País 6, moneda 1, cliente y carrito 0, una unidad,
geografía fiscal española por dirección de entrega. Para este artículo el
grupo fiscal 63 aplica el 21 % a España, sin restricciones de estado o código
postal. La combinación `9367` (calibre 49) repite el EAN del producto `498`,
con impacto de precio cero. Las 8 unidades observadas en stock se comprueban
por separado; la oferta no impone un límite de unidades.

`427094` es `id_specific_price`, no ID de producto, combinación ni regla de
catálogo. Se aplica a la combinación `9367`, España/EUR, cualquier tienda,
grupo o cliente, sin carrito, mínimo 1, vigencia abierta. Su precio fijo
`225.619800` es **sin IVA y antes** de la reducción porcentual de `0.15`.
También era elegible la regla de catálogo global 35 (fila `8593775`), con
15 % pero sin precio fijo. Según [PrestaShop 8.2.1](https://github.com/PrestaShop/PrestaShop/blob/8.2.1/classes/SpecificPrice.php),
la combinación exacta se ordena antes que la genérica; no se acumulan.
No hay prioridad particular para 498; la configuración general ordena
grupo, moneda, país y tienda después del cliente. El recálculo de contraste
da `225.619800 × 1.21 = 273.00` y `225.619800 × 1.21 × 0.85 = 232.05`,
redondeados a céntimos; coincide con la ficha. Esto no sustituye al motor nativo.

Auditoría de los mismos 100 GTIN en tienda 1: 56 aparecen en la combinación y
en su propio producto, 2 solo en combinación, 37 solo en un producto sin
combinaciones y 5 no tienen coincidencia en las columnas auditadas. La
repetición bajo el mismo producto no es ambigua cuando hay una sola
combinación exacta; nunca se elige entre varios productos o varios calibres
con el mismo GTIN.

**Antes de habilitar:** por defecto, el lector rechaza cuentas con permisos
distintos de lectura. El login de la auditoría tiene permisos amplios; una
transacción `READ ONLY` no convierte esa cuenta en una cuenta restringida.
La autorización expresa del responsable del 24/11/2026 permite utilizar
exclusivamente `ps_user_vytria_lect` en `ps_vytria`, sin cambiar sus grants,
mediante la excepción acotada y auditable descrita a continuación.
La auditoría se ejecutó vía SSH exclusivamente en transacciones `READ ONLY`.
El lector por lotes no necesita vista, rutina ni API. Abre una conexión,
verifica los permisos, y lee en una instantánea `REPEATABLE READ`, `READ ONLY`.
Reconstruye la selección de una única oferta, el impacto del calibre, impuestos
y reducción con `Decimal`. Para el grupo visitante excluye precios de clientes,
carritos y descuentos que requieren más de una unidad. Rechaza empates, IVA
territorial/compuesto, ecotasa y configuraciones o módulos no soportados.

1. El modo predeterminado `PRESTASHOP_PRICE_VALIDATION_MODE=php_review` exige
   verificar los overrides PHP desplegados y los módulos `altautofiller`
   (`actionPresentProduct`) y `revolutpayment` (`displayProductPriceBlock`).
   Los contrastes de ficha no prueban su comportamiento para todo el catálogo.
   Solo tras confirmar su efecto, establecer `PRESTASHOP_PRICE_CODE_AUDITED=1`.
   Existe un contrato alternativo explícito, descrito debajo, que no declara
   una inspección PHP ni modifica este indicador.
2. El modo predeterminado `PRESTASHOP_MYSQL_ACCOUNT_POLICY=select_only` exige
   `SELECT` sin permisos delegables ni roles. No modificar permisos de cuentas
   compartidas para facilitar una prueba. El lector sólo consulta `product[_shop]`,
   `product_attribute[_shop]`, `manufacturer`, `shop`, `shop_group`, `stock_available`, `country`, `currency`, `group`,
   `configuration`, `hook`, `hook_module`, `module`, `group_reduction`,
   `tax_rule`, `tax`, `specific_price`, `specific_price_priority`,
   `specific_price_rule`, `specific_price_rule_condition_group`,
   `specific_price_rule_condition` y `category_product` (prefijo real).
   Comprobar `SHOW GRANTS`, `APPLICABLE_ROLES` y `ENABLED_ROLES`; no validar
   los permisos intentando escribir. Para la cuenta amplia expresamente
   autorizada, configurar `PRESTASHOP_MYSQL_ACCOUNT_POLICY=authorized_broad`
   y `PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION` mediante el mismo mecanismo
   protegido de configuración. Su valor JSON contiene exactamente:
   `id`, `actor`, `at` (ISO con zona horaria, no futura), `host`, `port`,
   `user` y `database`. Los cuatro últimos deben coincidir con la conexión
   configurada; sólo se admite `user=ps_user_vytria_lect` y `database=ps_vytria`.
   No incluir contraseñas en este recibo ni guardar la configuración real en Git.
   Se contrasta además `CURRENT_USER()` y `DATABASE()` en la sesión real.
   La excepción no acepta grants sobre otras bases, permisos globales distintos
   de `USAGE`, roles, permisos delegables ni privilegios fuera de los previstos.
   Exige el ámbito literal `ps_vytria` (guion bajo escapado en los grants
   MariaDB), no una concesión con comodines de nombre de base.
   El lector mantiene TLS obligatorio, transacciones `READ ONLY`, consultas
   de precio `SELECT` parametrizadas y rollback; no ejecuta DML, DDL ni rutinas.
   En la conexión MariaDB autorizada comprueba también `@@tx_read_only=1`
   antes de leer precios.
   No acepta SQL proporcionado por el usuario y sólo admite los 100 EAN del
   manifiesto. El recibo, la amplitud real de los permisos, el cifrado y la
   verificación del certificado/servidor quedan en `connection_security`
   del precio persistido y se muestran en la administración de precios.
3. Inyectar por el gestor de secretos aprobado en el entorno del proceso
   `PRESTASHOP_MYSQL_HOST`, `_PORT` (3306 si se omite), `_DATABASE`, `_USER`,
   `_PASSWORD` y `_PREFIX`. No montar ni copiar el perfil MySQL del usuario SSH.
   Aquí los nombres abreviados comparten el prefijo `PRESTASHOP_MYSQL`.
   El modo predeterminado `PRESTASHOP_MYSQL_SSL_MODE=VERIFY_IDENTITY` exige
   `PRESTASHOP_MYSQL_SSL_CA` (ruta a la CA) y un nombre de servidor verificable.
   Sin CA y nombre verificables, configurar explícitamente `REQUIRED` (TLS
   obligatorio, **sin validar la identidad del servidor**). No hay degradación
   a texto claro. El lector comprueba `Ssl_cipher` antes de consultar precios.
   Desde el entorno del lector, comprobar `SELECT CURRENT_USER(), DATABASE()`
   y `SHOW SESSION STATUS LIKE 'Ssl_cipher'` por separado del smoke sin red.
   Configurar los IDs
   confirmados `PRESTASHOP_PRICE_SHOP_ID`, `_GROUP_ID`, `_COUNTRY_ID`,
   `_CURRENCY_ID` (prefijo `PRESTASHOP_PRICE`). Solo después de validar la
   instalación y obtener aprobación, `PRESTASHOP_PRICE_ENABLED=1`.

### Contrato acotado de reglas nativas (#159)

Rubén confirma que los descuentos de Vytria se configuran mediante reglas
nativas de catálogo por categoría, materializadas en precios específicos,
no mediante un cálculo propio de Panda. Es **confirmación del responsable
sobre la configuración comercial**, no inspección independiente del PHP.
La comprobación SQL de módulos/hooks tampoco acredita archivos desplegados.

`PRESTASHOP_PRICE_VALIDATION_MODE=native_rules_confirmed` requiere además
`PRESTASHOP_PRICE_NATIVE_CONFIRMATION`, un recibo JSON administrativo con
exactamente `id`, `actor`, `at`, `account_authorization_id`, `context` y
`claim=native_prestashop_catalog_rules`. La referencia de cuenta debe coincidir
con el recibo `authorized_broad`. El contexto autorizado es exactamente tienda
1, grupo público 1, España 6, EUR 1, cliente 0, cantidad 1, IVA incluido, sin
portes ni descuentos de carrito. Se conservan ambos recibos en la trazabilidad.
No establecer `CODE_AUDITED=1` para usar este contrato.

El lector contrasta los padres y condiciones de categoría de las ofertas
nativas aplicables. El porcentaje del padre (por ejemplo, 15) se compara con
la fracción materializada (0,15); **no se descuenta de nuevo**. El cálculo usa
una única fila de `specific_price` conforme a combinación, cantidad, regla
manual/catálogo y prioridades de ámbito, no el mayor porcentaje ni la suma
de ofertas. Un origen ausente, distinto, con condiciones no soportadas o
categorías incompatibles bloquea el precio afectado. Los hooks registrados
de cálculo/impuestos siguen bloqueando con módulo/hook y archivo concreto;
los de presentación quedan declarados, no inspeccionados. La mera presencia
de Panda o de un módulo de presentación no constituye evidencia de un
cálculo adicional. No se garantiza aquí el comportamiento del PHP/checkout.
La administración muestra «reglas nativas confirmadas; PHP no inspeccionado».
Una discrepancia de importe entre combinaciones del mismo producto requiere
revisión de modelo/color/calibre; no se presupone que cualquier combinación
sea únicamente otro calibre.

En la lectura real acotada de las relaciones SQL, el producto 498 tiene
oferta manual 427094 y alternativa materializada de regla 35/categoría 560
(15 %). El producto 7861 tiene oferta de combinación 384459/8425 (calibre 54)
y alternativa de regla 37/categoría 562 (25 %). Las ofertas manuales tienen
origen 0: no se presentan como generadas por esas reglas. Los importes actuales
deben obtenerse nuevamente con el piloto; estos IDs no fijan precios en código.

### Piloto administrativo con configuración privada

Si la identidad SSH no puede leer el Compose, el administrador puede ejecutar
[`scripts/validate_vytria_reader.py`](../../scripts/validate_vytria_reader.py)
desde un checkout limpio del SHA candidato revisado:

```sh
python3 scripts/validate_vytria_reader.py \
  --commit <SHA-candidato> --actor <revisor> --authorization-id <referencia-autorizacion>
```

El script resuelve `/etc/cloud-production/compose.private.json` con
`docker compose config --format json`, captura su salida sin publicarla y
no vuelve a interpretar los dólares de la contraseña. No lee `.mylogin.cnf`.
Por defecto requiere la comprobación PHP previa y `CODE_AUDITED=1`.
Para el contrato nativo autorizado añadir explícitamente
`--native-confirmation-id <referencia-confirmacion-Ruben>`. El piloto genera
su recibo auditable ligado a la autorización y contexto, sin establecer
`CODE_AUDITED=1` ni afirmar una revisión PHP.
Usa la imagen instalada sólo para dependencias y extrae el código del SHA con
`git archive`. Crea un único contenedor independiente, sin puertos ni monitores,
con usuario `1037:100`, raíz de sólo lectura y límites. La configuración MySQL
se envía por stdin, no como argumentos, variables Docker ni archivos.
La excepción y la activación se aplican **sólo dentro del proceso del piloto**.

Se toma mediante `SELECT` una muestra de los productos vigilados y sus
referencias Odoo persistidas; no se consulta Odoo ni se monta la base productiva
como destino de escritura. La instantánea coherente incluye únicamente los
grupos y reglas referenciados por esa vigilancia, copiados antes que sus hijos
para conservar las claves foráneas. No se ejecutan reglas ni monitores.
Los errores inesperados devuelven tipo y ubicación de código, sin mensajes
del driver, variables locales ni secretos. Se prueba primero `8056597435581` y, sólo si pasa,
el caso adicional `8056597684446` (7861/8425, ya incluido en el manifiesto)
y los otros 98 en lotes de hasta 25. No vuelve a leer los dos primeros.
Los resultados y el informe quedan en una
base aislada bajo `/var/lib/cloud-production-validation`, privada del
administrador. Se comprueban vigencia, fuente del comparador, conservación de
Odoo y registros duplicados. Se retira únicamente el contenedor del piloto.
También se exige conservar las 95 asociaciones por código y cinco referencias
explícitas; un cambio del reparto queda bloqueado y visible en el informe.
El script no modifica el Compose, no cambia grants y **no activa producción**.
No ejecutar con `sudo` sin la autorización administrativa correspondiente.

Tras acreditar la prueba y el CI/Docker del SHA definitivo, el administrador
incorpora al Compose protegido la excepción con su recibo y, si se usa el
contrato nativo, su modo y recibo de confirmación comercial, además de
`PRESTASHOP_PRICE_ENABLED=1`, conservando el respaldo. El despliegue aprobado
recrea sólo `abcd-app`; hay una interrupción técnica durante esa recreación,
aunque la vigilancia persistida siga `active`. No ejecutar un `compose up`
general ni alterar workers. El controlador exige avance de commit: no permite
reaplicar configuración al mismo SHA. Un cambio sólo de configuración requiere
un procedimiento administrativo aprobado, no un commit ficticio ni saltarse
el controlador. Después, usar una única sincronización del mecanismo
`VytriaPriceSync`, respetando su bloqueo o reutilizando una lectura ya vigente.

**Resultado medido (cálculo v3):** 13 consultas de lote, 1,61 s por SSH
(incluye lecturas y cálculo; no es una medición del contenedor). Se calcularon
100 precios: 95 por código de barras (43 activos con stock y 52 inactivos
sin stock) y 5 mediante referencia explícita de marca/modelo/color.
Se conservan los controles
de visibilidad de precio y de cantidad mínima.
Los 5 enlaces de referencia no son coincidencias de EAN. Se contrastaron
7 de los exactos con el escaparate ES/EUR: Prada
`8056597435581` 232,05 €, `0805289126584` 99 €, `0805289126591` 164 €,
`0889214635310` 222 €, `0889214669742` 312 €, `0889652429717` 201,60 € y
`0889652468983` 123 €. Los otros 93 importes son cálculos, no contrastes públicos.
Los resultados son evidencia de auditoría, no una sincronización de producción.

La instantánea conserva base, impacto, tipo/importe de reducción, regla,
contexto, IVA, redondeo, versión del cálculo y vigencia. El resultado vence
como máximo a las 24 horas o al siguiente cambio de oferta conocido. No se
transfiere disponibilidad entre calibres. Para una referencia de precio con
marca, modelo completo y color auditados, el calibre no cambia el precio ni se
exige conocerlo para aceptar la referencia. En el contrato nativo, importes
contradictorios entre calibres requieren revisión, sin elegir otro arbitrariamente;
las incidencias de otras combinaciones se muestran aparte.
Actividad y stock se muestran por separado y no condicionan el precio propio
ni crean puntos en su histórico.
«Con stock» significa cantidad positiva en la fila de la combinación exacta;
ni eso ni el precio calculado acreditan que pueda comprarse. Si falta una fila
de stock inequívoca en el ámbito de tienda o grupo, el stock queda sin verificar.

### Referencias de precio por marca/modelo/color

Se acreditó la coincidencia del modelo completo y código de color del
catálogo vigilado con el fabricante, referencia, nombre y características
Modelo/Color de cada ficha MySQL. No se acreditó que la divergencia de
código se deba al calibre, ni que ambos códigos sean la misma variante.

| EAN solicitado | Marca, modelo y color | Código original | Producto/combinación | Precio con IVA | Oferta / IVA |
|---|---|---|---|---:|---|
| `0889214095985` | Tom Ford FT0751N 01A | `889214095978` | 949/11091 | 189,00 € | 487154 / 21 % |
| `8056262600078` | Miu Miu MU 01YV 14L1O1 | `8056262600085` | 7917/8481 | 208,00 € | 387339 / 10 % |
| `8056262833902` | Ray-Ban RB4472 13592 | `8056262833919` | 9404/sin combinación | 82,00 € | Sin oferta / 21 % |
| `8056597669016` | Miu Miu MU 04UV 1AB1O1 | `8056262667484` | 9090/13347 | 256,00 € | 594137 / 10 % |
| `8056597669023` | Miu Miu MU 04UV VAU1O1 | `8056262667491` | 1615/13343 | 224,00 € | 593897 / 10 % |

El lector solo usa estas correspondencias explícitas si no hay coincidencia
del código solicitado. Revalida el código original, los IDs, la pertenencia a
la tienda, la marca y la referencia completa. Si la ficha cambia, hay varias
identidades o no se puede descartar un precio distinto en otra combinación,
deja el caso pendiente. No traslada actividad ni stock al EAN consultado.

### Consultas de identidad exacta (auditoría)

Se buscaron coincidencias exactas en `ps_product` y `ps_product_attribute`
(`ean13`, `upc`, `reference`), además de `ps_stock` y
`ps_supply_order_detail` (`ean13`, `upc`). Los cinco EAN sin coincidencia
son `0889214095985`, `8056262600078`, `8056262833902`,
`8056597669016` y `8056597669023`. Tres casos con IDs de referencia
**de Odoo**, no de PrestaShop:

| EAN | Variante Odoo | Plantilla Odoo | Formas a probar |
|---|---:|---:|---|
| `0889214095985` | 49928 | 48684 | `0889214095985`, `889214095985` |
| `8056262600078` | 90270 | 83941 | `8056262600078` |
| `8056597669016` | 56714 | 55254 | `8056597669016` |

Ejecutar con parámetros de texto. En los GTIN sin UPC equivalente se repite
el EAN como segundo parámetro. Los `LEFT JOIN` permiten distinguir una fila
global de una que realmente está vinculada a la tienda:

```sql
SELECT p.id_product, p.ean13, p.upc, p.reference, p.price AS global_price,
       ps.id_shop, ps.price, ps.active, ps.show_price, ps.minimal_quantity
FROM ps_product p
LEFT JOIN ps_product_shop ps ON ps.id_product=p.id_product AND ps.id_shop=%s
WHERE p.ean13 IN (%s,%s) OR p.upc IN (%s,%s) OR p.reference IN (%s,%s);

SELECT a.id_product, a.id_product_attribute, a.ean13, a.upc, a.reference,
       ats.id_shop, ats.price AS impact_ex_tax, ats.minimal_quantity,
       ps.price AS shop_price, ps.active, ps.show_price
FROM ps_product_attribute a
LEFT JOIN ps_product_attribute_shop ats
  ON ats.id_product_attribute=a.id_product_attribute AND ats.id_shop=%s
LEFT JOIN ps_product_shop ps ON ps.id_product=a.id_product AND ps.id_shop=%s
WHERE a.ean13 IN (%s,%s) OR a.upc IN (%s,%s) OR a.reference IN (%s,%s);
```

Parámetros: tienda `1` (dos veces en la segunda consulta), seguida de las
dos formas repetidas para cada campo. Una vez identificados `id_product` e
`id_product_attribute`, el cálculo toma `ps_product_shop.price` y el impacto
`ps_product_attribute_shop.price`; consulta `ps_specific_price` y
`ps_specific_price_priority` para la regla elegida, y `ps_tax_rule`/`ps_tax`
para el IVA. El importe específico, si sustituye la base, viene de
`ps_specific_price.price`. Falta la tabla o clave de correspondencia para
los cinco EAN anteriores; tampoco hubo coincidencias para sus variantes o
plantillas Odoo en `ps_cc_co_equivalenciaproductos.id_odoo` ni en
`ps_cc_co_equivalenciacombinaciones.id_odoo`. Los enlaces de precio de la tabla
anterior no sustituyen esa identidad exacta.

En SQLite, `prestashop_prices` guarda la última lectura, vencimiento, último
intento e incidencia; `prestashop_price_history` guarda cambios de precio,
oferta o contexto. Las reconfirmaciones solo actualizan las fechas del estado
vigente. Ambas tablas se vinculan por EAN a `vytria_prices` y al histórico
existente de Odoo; no alteran dicha tarifa ni la identidad/disponibilidad del
comparador. La sección de administración distingue las dos fuentes.

## Referencia Odoo y operativa anterior

La integración Odoo sustituye la importación manual de precios propios. Es **solo de lectura**:
no se modifica nada en Odoo ni en PrestaShop.

## Fuente del precio

Auditoría de solo lectura sobre `DiagonalDBProd` (PostgreSQL 12.8, sesión `READ ONLY`):

- Tarifa real de Vytria España: **ID 59** («Vytria TARIFA_ESPAÑA», EUR, `website_id=10`, canal
  `prestashop`). La 17 es la versión anterior (`website_id=4`).
- Precio final: tabla `prestashop_pricelist_item_pricelist` (`pricelist_id=59`), unida a
  `prestashop_pricelist_item` (`product_id` → `product_product`). Se guarda **tal cual**: ya incluye
  los descuentos de la tarifa y el IVA. No se vuelve a descontar ni se suma IVA.
- Descuento de la tarifa: `list_price × (1 − descuento %)`, con el descuento del ítem
  `1_pricelist_map` de la tarifa 59 asociado a los mapas del producto
  (`product_pricelist_map_product_template_rel`), redondeado a 1,00. El campo
  `product_template.pricelist_map_id` **no** es este mapa.
- IVA: un único impuesto de venta por producto con `price_include = True` (10 % o 21 %). El
  porcentaje se guarda solo como información.
- Los 12 EAN auditados coinciden exactamente entre `list_price`/mapa, precio de integración y
  precio sincronizado (probado también contra Odoo real, con SQLite temporal).
- No se usan `diagonal_vytria_int_catalog_item*` (0 filas) ni el canal 5.

**Pendiente: contraste directo con los precios publicados en PrestaShop. No está validado.**

### Auditoría del ciclo diario de vigilancia

Se ha revisado el lector `SqlOdooPriceReader`, las consultas `PRODUCTS_SQL`/`PRICES_SQL`,
la validación de impuestos/mapas, los scripts de auditoría y sus pruebas, y las
integraciones Odoo existentes. El código disponible no contiene un cliente directo
del catálogo/checkout de PrestaShop. No se han consultado servicios reales en esta revisión.

**Decisión aprobada:** reutilizar la tarifa 59 como fuente operativa de Vytria,
identificada siempre como **Odoo tarifa 59**, sin afirmar que se haya contrastado
el precio publicado en la tienda. La validación de la fórmula de Odoo no acredita
por sí sola promociones o reglas particulares del checkout de PrestaShop.

Alternativas no utilizadas:

- `product_template.list_price`: precio base; no representa por sí solo el PVP
  descontado de la tarifa España. Solo sirve como comprobación, no como sustituto.
- Tarifa 17, canal 5 y tablas de integración vacías: no son la fuente auditada
  actual. No se crea una nueva integración ni se recalculan descuentos/IVA.
- API o scraping de PrestaShop: no existe un lector reutilizable ni evidencia
  de configuración/autorización para validarlo aquí. Contraste pendiente.
- Excel: contingencia supervisada existente, no fuente automática diaria.

## Datos guardados (SQLite del comparador, volumen persistente)

| Tabla | Contenido |
|---|---|
| `vytria_prices` | Precio vigente por EAN: precio, moneda, IVA incluido, % IVA, origen, tarifa, IDs de origen (producto, plantilla, catálogo, fila de precio), fecha de origen, precio recalculado, estado de validación, fechas de primera/última variación y de sincronización. |
| `vytria_price_history` | Una fila por cambio (precio, IVA, moneda, estado). Nunca se borra. |
| `vytria_sync_runs` / `vytria_sync_issues` | Registro de cada ejecución y de las incidencias por EAN. |
| `vytria_sync_settings` | `auto_enabled` (por defecto `0`). |
| `vytria_price_alerts` | Alertas de cambios relevantes de precios validados vigilados, enlazadas de forma única al cambio de histórico. Se muestran junto a las alertas de competencia; sin envío de correo. |

Solo se sincronizan los EAN del catálogo del comparador (consulta acotada por código de barras;
los UPC-12 se buscan con y sin cero inicial). La escritura es incremental: si el origen no cambia,
solo se actualiza `synced_at` y no se crea histórico.

## Validación cruzada y estados

El precio de origen nunca se modifica; la discrepancia se registra. Solo `validated` se compara
con la competencia (`comparable = 1`).

| Estado | Significado |
|---|---|
| `validated` | El precio coincide con la fórmula de la tarifa (tolerancia 0,01 €). |
| `price_mismatch` | Difiere del recálculo. Se guarda el precio recibido y el esperado. |
| `no_map` | El producto no tiene mapa con regla en la tarifa 59. |
| `multiple_maps` | Mapas con resultados distintos. |
| `exceptional_rule` | Descuento negativo (p. ej. `Vytria CT0649S`, −300 %), nombre del mapa distinto del % aplicado (p. ej. «65 %» que en la 59 aplica 45 %), recargo, cantidad mínima, fechas, regla por producto o cálculo no `formula`. |
| `tax_issue` | Sin IVA, más de un IVA, impuesto no porcentual o no incluido en el precio. |
| `currency_issue` | Moneda distinta de EUR. |
| `discontinued` | Variante/plantilla inactiva o no vendible. |

Incidencias que **no sobrescriben** el último precio confirmado: `not_in_odoo`, `no_price`
(sin fila, inactiva o ≤ 0), `multiple_prices`, `ambiguous_product`, `unexpected_catalog`.
En el comparador, un EAN sincronizado sin precio `validated` queda sin precio propio (no se usa el
Excel); un EAN nunca sincronizado conserva el precio importado manualmente.

## Ejecución

- **Manual**: botón «Actualizar precios Vytria» (Análisis comercial → Comparador de precios).
- **Diaria**: interruptor «Sincronización diaria automática», **desactivado por defecto**. Solo
  actúa `python vytria_price_sync.py --scheduled` (se omite si está desactivado o si hubo una
  sincronización correcta en las últimas 24 h, salvo intervalo configurado). Esta PR **no** crea ningún disparador ni servicio;
  activarlo en producción requiere un job aprobado que invoque ese comando.
- **Concurrencia**: `FileLock` junto a la base; un segundo intento recibe «sincronización en curso».
- **Errores y reintentos**: 3 intentos con espera 2 s / 4 s ante fallos de conexión; si falla, se
  conservan los precios y se registra solo el tipo de error (sin host ni credenciales).
- **Conexión**: `load_db_config()` (`DB_*`), `default_transaction_read_only=on`,
  `SET TRANSACTION READ ONLY`, `statement_timeout=30000`; solo `SELECT`.

## Contingencia

La importación Excel de precios queda en un desplegable «Contingencia administrativa». Un precio
sincronizado desde Odoo tiene prioridad sobre el manual para el mismo EAN.

## Limitaciones

- Sin precio promocional ni disponibilidad: la tarifa 59 ya incorpora los descuentos y la
  disponibilidad no procede de esta fuente.
- Mercado fijo `ES` y moneda EUR.
- Los productos con mapas especiales quedan marcados para revisión y no entran en la comparación
  hasta confirmarse.
- La muestra auditada (12 EAN) no contenía mapas 65 % ni CT0649S: se cubren con datos de prueba.
- El Dockerfile copia `vytria_price_sync.py`; sin él la página del comparador no arranca.

## Revisión previa al merge (PR #88)

- **Vigencia**: un precio sincronizado solo participa en comparaciones si es alidated, comparable, sin incidencia abierta y con sincronización de menos de 7 días (SYNCED_PRICE_MAX_AGE_DAYS). Si no, se muestra como stale (incidencia) o xpired (antiguo) y no se compara.
- **Incidencias**: ante un fallo de un EAN con precio previo se conserva precio e histórico, se marca `stale_since`/`stale_reason` y no se actualiza `synced_at`. Se limpia al volver a sincronizar correctamente.
- **Interfaz**: el botón lanza la sincronización en un hilo en segundo plano (bloqueo de fichero junto a la SQLite); la página se refresca cada 2 s mientras corre. Las ejecuciones huérfanas en `running` se cierran como `error` en la siguiente.
- **Migración**: solo `CREATE TABLE IF NOT EXISTS` de tablas `vytria_*`; no se tocan tablas existentes. Ruta SQLite (PR #87) intacta.
- **Pendiente**: contraste con PrestaShop y disparador diario (desactivado).

## Operativa diaria de productos vigilados

- `python vytria_price_sync.py --watched`: actualización **manual supervisada**
  exclusivamente de `watch_products.active=1`. También disponible, con confirmación,
  en Vigilancia automática → Precios y monitorización → Precio Vytria y ciclo diario.
- `python vytria_price_sync.py --scheduled --watched`: entrada diaria de los
  vigilados; no hace nada sin `auto_enabled=1`. Reutiliza el lector, bloqueo,
  ejecuciones, incidencias, precios e históricos del sincronizador anterior.
- `PriceMonitor.tick()` llama a esta sincronización **antes** de procesar los
  competidores habilitados. Requiere tanto el interruptor de servidor
  `PRICE_MONITOR_SCHEDULE_ENABLED` como `auto_enabled=1` de Vytria. Ninguno se
  activa al importar módulos, abrir la pantalla o desplegar.
- Frecuencia inicial: **24 horas**. Se aprovecha la fecha persistente por EAN,
  no solo la última ejecución global: añadir un vigilado sin precio provoca
  su sincronización en la siguiente pasada aunque el resto ya esté actualizado.
  Los intervalos configurados menores se respetan; uno mayor no amplía la
  vigencia diaria de los productos vigilados.
- Lectura acotada al conjunto vigilado, sin límite estructural de 50 productos.
  Las ejecuciones legacy/manuales de todo el catálogo siguen disponibles.
- Ante fallos de conexión hay tres intentos 2/4 s; una ejecución diaria parcial
  o fallida tiene además una pausa persistente de **30 min** antes de reintentar.
  Una ejecución interrumpida se recupera mediante el bloqueo existente y se
  marca como interrumpida; el fingerprint evita duplicar históricos/alertas.
- Los precios **vigilados** caducan a las 24 h exactas (o antes si la vigencia
  global del comparador es menor); se rechazan fechas futuras o ilegibles.
  Diagnósticos, inventario y dashboard reutilizan `vytria_price_states`.
  La vigencia legacy de productos no vigilados no cambia.
- Si falla la lectura, se marca como desactualizado el último precio vigilado:
  no se cambia su valor, `synced_at` ni histórico, pero no se usa como vigente.
  Un valor vacío, no positivo, infinito/NaN, ambiguo o inconsistente nunca
  reemplaza un precio vigilado previamente validado. La incidencia conserva
  el motivo y, ante discrepancia, el precio recibido. Una lectura válida
  posterior limpia el estado, incluso si el precio no ha cambiado.
- Una incidencia de Vytria no impide conservar observaciones de competencia:
  las diferencias y nuevas alertas de «más barato que Vytria» no se calculan
  con el precio propio desactualizado. No hay fallback silencioso a Excel
  cuando ya existe un precio sincronizado del EAN.
- El histórico de Vytria y competencia y sus gráficos ya existentes se
  reutilizan. Los cambios válidos vigilados que superan el umbral vigente
  generan una alerta persistente única por fila histórica; una simple
  reconfirmación del mismo precio no genera otra.

Migración: se añade únicamente `vytria_price_alerts` con
`CREATE TABLE IF NOT EXISTS`, sin modificar/borrar datos anteriores ni exigir
PostgreSQL para el comparador. PostgreSQL solo es el origen Odoo, como antes.
