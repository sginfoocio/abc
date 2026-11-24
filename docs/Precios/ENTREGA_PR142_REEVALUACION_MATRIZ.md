# PR #142 - reevaluación, recuperación y matriz

## Evidencias

El paquete histórico `docs/Precios/100_EAN/Paquete_revision_7_casos_20261117.zip`
fue exportado el 17/11/2026 a las 22:45:34 UTC. No representa inventario actual.
Las capturas Amevista, Visual-Click y Domanci son HTML JSON-LD sanitizados
parciales; no acreditan mercado, vigencia comercial ni cobertura actual.

## Reevaluación local Optical H

El servicio `price_saved_reevaluation.py` permite:

```sh
python price_saved_reevaluation.py --db <sqlite> --evidence <json-o-zip> --request-id 1
```

Aplicación explícita:

```sh
python price_saved_reevaluation.py --db <sqlite> --evidence <json-o-zip> \
  --request-id 1 --apply --approved --actor "<responsable>" \
  --note "<motivo>" --commit "<40-hex>"
```

No realiza HTTP. Conserva `sampled_at`, URL, procedencia y caducidad. Solo aplica
mercado ES e IVA incluido a una muestra idéntica, posterior a la solicitud y no
caducada. Una repetición no crea otra oferta ni otra auditoría.

La UI de Vigilancia muestra la misma previsualización y exige responsable, motivo,
commit y confirmación antes de aplicar.

## Recuperación

`price_recovery.preview_manual_reviews()` y `apply_manual_reviews()` trabajan por
IDs exactos, bloqueos de continuidad y competidor, recibos, reinicio y muestras
persistentes. El resumen conserva las tres categorías históricas y añade:

1. Ya resuelta.
2. Reevaluable localmente.
3. Recuperable para lectura gobernada.
4. Captura/evidencia adicional necesaria.
5. Revisión por contradicción real.
6. Excluida por vigencia o controles.

La aplicación gradual aplica primero el ID solicitado y después solo los
elegibles. No reprograma pedidos, no acepta revisiones pendientes ni realiza
consultas externas.

## Matriz

Regeneración offline:

```sh
python scripts/update_price_1400_matrix.py
```

Artefactos:

- `docs/Precios/100_EAN/MATRIZ_100_EAN_14_COMPETIDORES.csv`
- `docs/Precios/100_EAN/MATRIZ_100_EAN_14_RESUMEN.json`

Contienen exactamente 100 EAN conservados con ceros iniciales y 1.400 pares
únicos. La matriz no atribuye oferta vigente ni ficha verificada sin evidencia.

## Validación

Ejecutar:

```sh
python -m pytest -q tests/test_price_saved_reevaluation.py \
  tests/test_price_recovery.py \
  tests/test_manual_review_continuity_repro.py \
  tests/test_price_1400_matrix.py
```

## Procedimiento posterior al despliegue

1. Inventario actual de solicitudes manuales y diagnósticas.
2. Aplicar primero un ID por mecanismo confirmado.
3. Comprobar idempotencia, concurrencia y reinicio.
4. Confirmar vigencia, permisos, robots y presupuesto.
5. Ampliar solo a pares elegibles con autorización expresa.

No se ha ejecutado recuperación, HTTP comercial ni sincronización Vytria/Odoo.
