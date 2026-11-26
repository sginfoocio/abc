"""Bounded automatic submission of Luxottica purchase-order requests."""
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from threading import Event

from filelock import FileLock, Timeout
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app_exports import _legacy_luxottica_keys, _unindexed_image_exists
from app_pages.provider_order_actions import order_request_evidence
from app_pages.provider_order_snapshot import MADRID, NONE, associations, image_evidence, local_date
from db_config import load_db_config
from image_repository import repository_root
from kering_images import data_root as kering_root
from luxoptica_auto_upload import _upload_to_luxoptica, write_luxottica_upload_file
from luxottica_config import LuxotticaConfig, validate_portal_url
from process_activity import record_process
from provider_images import (
    LUXOTTICA_CONFIRMATION_PREFIX, LUXOTTICA_ORDER_MODE, NEEDS_REVIEW, READY, SUBMITTED, SUBMITTING,
    ProviderSettings, ProviderStore, ean_key, ean_valid,
    normalize_eans,
)
from provider_odoo_orders import MAX_ORDER_DAYS, read_purchase_page
from provider_order_policy import DEFAULT_ORDER_START_DATE, get_order_start_date, provider_data_root
from repository_storage import state_root


PROVIDER = "luxottica"
SUPPLIER_ID = 570
MAX_ORDER_BATCH_CODES = 10
MAX_PREFLIGHT_CODES = 250
SETTINGS = ProviderSettings(PROVIDER, max_eans_per_request=MAX_ORDER_BATCH_CODES)
ACTOR = "programador_luxottica"
HEALTHY_RESULTS = {"desactivado", "sin_referencias_pendientes", "limite_diario", "otro_ciclo_activo",
                   "enviado_confirmado"}


def order_pages(engine, links, start, today):
    while start <= today:
        end = min(today, start + timedelta(days=MAX_ORDER_DAYS - 1))
        cursor = None
        pending = None
        while True:
            page, cursor = read_purchase_page(engine, links, (PROVIDER,), start, end, before=cursor)
            complete = []
            for order in page:
                if pending is not None:
                    if (pending["id"], pending["state"], pending["line_count"]) != (
                            order["id"], order["state"], order["line_count"]):
                        raise ValueError("pedido_cambio_durante_paginacion")
                    order["lines"] = pending["lines"] + order["lines"]
                pending = order if len(order["lines"]) != order["line_count"] else None
                if pending is None:
                    complete.append(order)
            if complete:
                yield complete
            if cursor is None:
                if pending is not None:
                    raise ValueError("pedido_incompleto")
                break
        start = end + timedelta(days=1)


def eligible_orders(orders, images, excluded):
    scoped = [order for order in orders if order["provider"] == PROVIDER and order["partner_id"] == SUPPLIER_ID
              and order["state"] in {"purchase", "done"} and not order["order_identity_conflict"]]
    if not scoped:
        return
    evidence, notice = image_evidence(scoped, state_root(images) / ".catalog.sqlite3", images)
    if notice:
        raise ValueError("catalogo_nas_no_verificable")
    for order in scoped:
        groups = {}
        for line in order["lines"]:
            if ean_valid(line["ean"]):
                groups.setdefault(ean_key(line["ean"]), []).append(line)
        eligible = []
        for key, lines in groups.items():
            info = evidence.get(key)
            if (key in excluded or not info or info["status"] != NONE or info["incomplete"] or info["issues"]
                    or any(line["barcode_conflict"] or not line["sku"] or not line["model"]
                           or not line["color"] for line in lines)
                    or len({(line["product_id"], line["sku"]) for line in lines}) != 1
                    or _unindexed_image_exists(images, lines[0]["ean"])):
                continue
            eligible.append(lines)
        if eligible:
            yield order, eligible


def _stored_order(engine, links, store, request_id, images, excluded, cutoff):
    sources = [entry["evidence"] for entry in store.source_evidence(request_id) if entry["source"] == "odoo"]
    identities = {(entry["order_id"], entry["order"], entry["date"]) for entry in sources}
    if len(identities) != 1:
        raise ValueError("pedido_no_identificado")
    order_id, name, stamp = identities.pop()
    utc_date = date.fromisoformat(stamp)
    first = max(cutoff, utc_date)
    last = utc_date + timedelta(days=1)
    cursor = None
    all_lines = []
    while True:
        orders, cursor = read_purchase_page(engine, links, (PROVIDER,), first, last,
                                            order_id=order_id, before=cursor)
        if len(orders) != 1 or orders[0]["name"] != name:
            raise ValueError("pedido_no_disponible")
        order = orders[0]
        all_lines.extend(order["lines"])
        if cursor is None:
            break
    if len(all_lines) != order["line_count"] or local_date(order["date_order"]) < cutoff:
        raise ValueError("pedido_fuera_de_alcance")
    codes = {item["ean"] for item in store.get(request_id)["eans"]}
    selected = {ean_key(code) for code in codes}
    order["lines"] = [line for line in all_lines if ean_key(line["ean"]) in selected]
    current = next(eligible_orders([order], images, excluded), None)
    actual = {(str(line["id"]), str(line["product_id"]), line["ean"], line["sku"],
               str(line["quantity"]), line["model"], line["color"], line["caliber"])
              for group in current[1] for line in group} if current else set()
    expected = {(str(line["line_id"]), str(line["product_id"]), line["ean"], line["sku"],
                 str(line["quantity"]), line["model"], line["color"], line["caliber"])
                for line in sources}
    if not actual or actual != expected:
        raise ValueError("pedido_o_cobertura_cambiaron")
    return sorted(codes)


def run_cycle(engine, root, images, stopping, *, config=None, submit=None):
    config = config or LuxotticaConfig(root)
    submit = submit or _upload_to_luxoptica
    if not config.load().enabled or stopping.is_set():
        return "desactivado"
    root.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(root / "luxottica-order-scheduler.lock"), timeout=0)
        with lock:
            return _run_locked(engine, root, images, stopping, config, submit)
    except Timeout:
        return "otro_ciclo_activo"


def _run_locked(engine, root, images, stopping, config, submit):
    settings = config.load()
    if not settings.enabled or stopping.is_set():
        return "desactivado"
    validate_portal_url(settings.url)
    if not settings.username or not settings.password:
        raise ValueError("credenciales_no_configuradas")
    links, notices = associations(root, kering_root())
    if notices or [(key, partner) for key, partner, _ in links if key == PROVIDER] != [(PROVIDER, SUPPLIER_ID)]:
        raise ValueError("asociacion_570_no_verificada")
    store = ProviderStore(root)
    requests = [r for r in store.list_requests(PROVIDER, {SUBMITTING, NEEDS_REVIEW, READY})
                if r["mode"] == LUXOTTICA_ORDER_MODE]
    for request in requests:
        if request["state"] == SUBMITTING:
            store.transition(request["id"], NEEDS_REVIEW, expect=SUBMITTING, error="envio_interrumpido_ambiguo")
    if any(r["state"] in {NEEDS_REVIEW, SUBMITTING} for r in requests):
        return "envio_pendiente_de_revision"
    if store.requests_today(PROVIDER) >= SETTINGS.max_requests_per_day:
        return "limite_diario"
    cutoff = max(DEFAULT_ORDER_START_DATE, get_order_start_date(root))
    today = datetime.now(MADRID).date()
    history = Path(__file__).resolve().parent / "docs" / "Luxoptica"
    legacy = _legacy_luxottica_keys(history)
    ready = [r["id"] for r in reversed(requests) if r["state"] == READY]
    live = store.live_keys(PROVIDER)
    if not ready:
        for page in order_pages(engine, links, cutoff, today):
            for order, groups in eligible_orders(page, images, live | legacy):
                for offset in range(0, len(groups), MAX_ORDER_BATCH_CODES):
                    if stopping.is_set() or not config.load().enabled:
                        return "desactivado"
                    batch = groups[offset:offset + MAX_ORDER_BATCH_CODES]
                    items = order_request_evidence({**order, "lines": [line for group in batch for line in group]})
                    ready = store.plan(SETTINGS, items, mode=LUXOTTICA_ORDER_MODE,
                                       order_evidence=items, reviewer=ACTOR)["created"]
                    if ready:
                        break
                if ready:
                    break
            if ready:
                break
    if not ready:
        return "sin_referencias_pendientes"
    rid = ready[0]
    try:
        codes = _stored_order(engine, links, store, rid, images, legacy, cutoff)
    except ValueError as error:
        store.transition(rid, SUBMITTING, expect=READY)
        store.transition(rid, NEEDS_REVIEW, expect=SUBMITTING, error=str(error))
        return "seleccion_para_revision"
    settings = config.load()
    if not settings.enabled or stopping.is_set():
        return "desactivado"

    def before_send():
        return (not stopping.is_set() and config.load() == settings
                and _stored_order(engine, links, store, rid, images, _legacy_luxottica_keys(history), cutoff) == codes)

    with TemporaryDirectory(prefix="luxottica-order-") as temp:
        path = Path(temp) / f"upc-products-images-request-{rid}.txt"
        write_luxottica_upload_file(path, codes)
        store.transition(rid, SUBMITTING, expect=READY)
        ok, message = submit(settings.url, settings.username, settings.password, path, settings.email, True, False,
                             before_send=before_send)
        if not ok or not message.startswith(LUXOTTICA_CONFIRMATION_PREFIX):
            if ok:
                message = "envio_sin_confirmacion"
            store.transition(rid, NEEDS_REVIEW, expect=SUBMITTING, error=message)
            return message
        store.transition(rid, SUBMITTED, expect=SUBMITTING, detail=message)
    return "enviado_confirmado"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-file", type=Path, help="Verifica el portal hasta antes del envío.")
    args = parser.parse_args()
    root = provider_data_root()
    config = LuxotticaConfig(root)
    if args.preflight_file:
        settings = config.load()
        lines = args.preflight_file.read_text(encoding="utf-8-sig").splitlines()
        codes, invalid = normalize_eans(lines)
        if invalid or not codes or sum(bool(line.strip()) for line in lines) > MAX_PREFLIGHT_CODES:
            parser.error("Indica un TXT con entre 1 y 250 UPC/EAN válidos.")
        ok, message = _upload_to_luxoptica(settings.url, settings.username, settings.password,
                                          args.preflight_file, settings.email, True, False, preview_only=True)
        print(message)
        return 0 if ok else 1
    stopping = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    db = load_db_config()
    engine = create_engine(URL.create("postgresql+psycopg2", username=db.user, password=db.password,
                                      host=db.host, port=db.port, database=db.database),
                           connect_args={"connect_timeout": 5}, pool_timeout=5)
    try:
        while not stopping.is_set():
            interval = 60
            try:
                settings = config.load()
                interval = settings.interval_minutes if settings.enabled else 1
                with record_process("luxoptica-upload", enabled=settings.enabled, interval=interval * 60) as receipt:
                    result = run_cycle(engine, root, repository_root(), stopping, config=config)
                    receipt["result"] = "Correcto" if result in HEALTHY_RESULTS else "Parcial"
                    receipt["counts"] = {"Envíos confirmados": int(result == "enviado_confirmado")}
                    if result not in HEALTHY_RESULTS:
                        receipt["counts"]["Bloqueos de envío"] = 1
            except Exception as error:
                print(f"Luxottica: ciclo detenido ({type(error).__name__})", flush=True)
            stopping.wait(interval * 60)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
