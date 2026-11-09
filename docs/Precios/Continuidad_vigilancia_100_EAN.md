# Continuidad de vigilancia: mismos 100 EAN y seis tiendas

## Estado y alcance

La configuración versionada en [`price_watch_continuity.json`](../../price_watch_continuity.json)
está en estado `prepared`, requiere activación explícita y no contiene fecha de inicio.
No se ha activado esta vigilancia ni se ha modificado, reiniciado o prolongado el
piloto original. La continuidad conserva su propio historial y controles en las
tablas `price_continuity`, `price_continuity_pairs` y `price_continuity_checks`.

El manifiesto es exactamente [`price_trial_manifest.json`](../../price_trial_manifest.json):
100 EAN como cadenas de texto, incluidos sus ceros iniciales, y las seis tiendas:

1. Óptica Óptima
2. Amevista
3. Visio Factory
4. Edel-Optics
5. Domanci
6. ConGafasDeSol

Su SHA-256 es `be618e2e9a004cf0df7f9dba9163a1a53114b603012ee063c89ee11c50c31887`.
No se incorporan otros productos, tiendas ni colas. Vytria/Odoo conserva el lector
de solo lectura y solo puede recibir esa lista exacta de 100 EAN.

## Frecuencias, presupuesto y controles

- Ofertas verificadas: siguiente comprobación no más tarde de 24 horas desde la
  última lectura correcta, respetando cualquier intervalo más estricto existente
  y la fecha de vigencia persistida.
- Negativos, bloqueos y revisiones: se conservan sus plazos `not_before`,
  Retry-After, restricciones y estado. Un reinicio no convierte una ficha pendiente
  o no localizada en inexistente, ni acepta revisiones automáticamente.
- Presupuesto efectivo: el menor entre los límites actuales, los guardados al
  activar y 200 HTTP en cualquier ventana móvil de 24 horas por competidor;
  máximo de 10 por minuto y pausa mínima efectiva de 3 segundos. Los límites
  actuales más estrictos prevalecen.
- Robots y redirecciones se reservan y registran por petición. Al agotar presupuesto
  se persiste la hora de reintento; el trabajador no fuerza ni acumula consultas.
- Cada turno atiende como máximo 20 pares y 240 segundos entre pares. Las fichas
  verificadas se priorizan sobre descubrimiento; los presupuestos y guardas
  existentes siguen siendo el límite efectivo.
- Reinicios conservan estado y fecha siguiente. Una reserva interrumpida de
  resultado incierto pasa a revisión y no se repite automáticamente.
- Inicio, responsable real, commit y límites quedan guardados al activar. La UI
  expone preparación, estado, latido, siguiente sincronización, fechas por par y
  controles explícitos de pausa, reanudación y parada. Cada control queda auditado.

La preparación requiere el cierre del piloto, los mismos productos y tiendas
activos y un latido reciente. La activación exige un responsable, un commit de
Actions y confirmación explícita en la interfaz. Ninguna configuración, despliegue
o prueba sintética constituye activación.

## Métricas

El informe separa selección (600 pares y 100 EAN), pares con HTTP real, ofertas
reutilizadas, aplazadas, restringidas y en revisión, ofertas comparables vigentes
y caducadas, y peticiones HTTP comerciales y de robots. Los recibos HTTP se
deduplican por ID, y las reservas sin resultado se muestran separadas de las
respuestas o errores confirmados. `done` no se usa como sinónimo de consultado.

## Lectura de la evidencia del piloto

La fuente local facilitada fue `docs/Precios/100_EAN/piloto-72h-historial (2).json`.
El piloto expiró el 09/11/2026 a las 16:26, hora de Madrid; la recuperación se
integró mediante PR #122, commit `0e1bf35a71481d5814d4514c02725160908bd79c`.
El contexto de cierre registra 75 ofertas comparables vigentes en 37 EAN y
50 pares cuya última lectura ya estaba fuera de vigencia.

Comprobaciones manuales limitadas a recibos ya guardados:

- Edel-Optics, EAN `0889214257314`: reutilizado el 07/11; vigencia hasta las
  21:16 y nueva lectura el 08/11. Es el ejemplo de reutilización seguida de
  vencimiento, sin solicitar una ficha nueva.
- ConGafasDeSol, EAN `8056597886918`: el recibo muestra una petición a
  `robots.txt` y aplazamiento por presupuesto diario.
- ConGafasDeSol, EAN `0889652359700`: discrepancia con el EAN publicado
  `8056376384482`; se mantiene en revisión manual. No se acepta la identidad.
- Las sincronizaciones Vytria 9, 10 y 11 registran cuatro incidencias cada una.
  Los recibos disponibles no exponen los EAN afectados, por lo que no se puede
  confirmar si las cuatro incidencias corresponden a los mismos productos.
  No se inventan los EAN ni se repite una sincronización para averiguarlo.

Los 596 IDs de recibo HTTP únicos del archivo suman 596 peticiones. La
clasificación local por ruta de URL da 312 peticiones comerciales y 284 a
`/robots.txt`, mientras el contexto verificado proporcionado indica 323 y 273.
La diferencia de 11 no se ha podido reconciliar con los recibos disponibles;
no se presenta como resuelta ni se cambia el dato fuente por suposición.

La causa técnica de las lecturas repetidas de robots es que `WebFetcher` guardaba
las reglas solo en memoria de instancia, mientras los flujos de monitorización y
localización crean fetchers en diferentes trabajos. Se incorpora caché SQLite
persistente por origen y agente, con caducidad HTTP respetada (hasta 24 horas).
La respuesta se reutiliza solo mientras está vigente; cada URL y agente sigue
evaluándose contra las reglas. Respuestas de error no se cachean y `no-store`,
`no-cache`, `private`, `Vary: *`, `Age`, `max-age`, `s-maxage` y `Expires` se
respetan.

No se hizo ninguna petición real a comercios ni a Vytria/Odoo durante esta
preparación. Para activar, revisar primero el manifiesto y la configuración
descargable, obtener por separado la aprobación final para merge, despliegue y
activación, respetar la aprobación habitual de producción y desplegar una sola
vez. Después de una activación autorizada, verificar versión, salud, latido,
próxima ejecución y primeros recibos reales.
