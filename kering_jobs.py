from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import logging
import signal
from threading import Event
import time
from zoneinfo import ZoneInfo

from filelock import Timeout
from sqlalchemy import create_engine, URL

from db_config import load_db_config
from kering_images import (
    ConfigStore, ImageStore, DEFAULT_SETTINGS, configured_orders, batch_lock, execute_run,
    purchase_status, trial_validated, data_root, IN_PROGRESS, line_signature, NON_TECHNICAL_REASONS,
)
from kering_portal import KeringPortal


AUTO_ORIGIN = "Autom\u00e1tico"
ERROR_BACKOFF = 300
MADRID = ZoneInfo("Europe/Madrid")
LOGGER = logging.getLogger(__name__)


def automatic_decision(order, status, attempts, config, now):
    """Keep automatic retries bounded without changing manual processing or historical attempts."""
    if not status["needs_processing"]:
        return "sin_pendientes"
    attempts = [attempt for attempt in attempts if attempt["status"] not in {"Pendiente", IN_PROGRESS}]
    if not attempts:
        return "elegible"
    latest = attempts[0]
    if line_signature(order) != line_signature(json.loads(latest["snapshot"])):
        return "elegible"
    if latest["status"] == "Completo":
        return "elegible"
    errors = 0
    for attempt in attempts:
        if line_signature(order) != line_signature(json.loads(attempt["snapshot"])):
            break
        reasons = {item.get("reason", "") for item in json.loads(attempt["results"]).values()}
        technical = attempt["status"] in {"Error", "Interrumpido"} or bool(reasons - NON_TECHNICAL_REASONS)
        if not technical:
            break
        errors += 1
    if not errors:
        return "revision_parcial"
    if errors >= config.get("auto_max_technical_attempts", DEFAULT_SETTINGS["auto_max_technical_attempts"]):
        return "revision_limite_tecnico"
    last = latest["ended"] or latest["started"]
    hours = config.get("auto_retry_interval_hours", DEFAULT_SETTINGS["auto_retry_interval_hours"])
    if now < last + float(hours) * 3600:
        return "espera_reintento"
    return "elegible"


def order_day(order):
    instant = datetime.fromisoformat(order["date_order"])
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(MADRID).date()


def check_permission(store, config, order_ids):
    ids = set(order_ids)
    if config.get("test_mode", True):
        permitted = set(config.get("test_order_ids", []))
        if not ids or len(ids) > 2 or not ids <= permitted:
            raise ValueError("Seleccione y autorice uno o dos pedidos de prueba")
    elif not config.get("full_lot_validated") or not trial_validated(store, config):
        raise ValueError("Falta validar descarga, reutilizacion e historial del piloto")


def prepare_selection(store, config, orders, start, end):
    check_permission(store, config, [order["id"] for order in orders])
    start = max(start, date.fromisoformat(config.get("cutoff_date", DEFAULT_SETTINGS["cutoff_date"])))
    if not orders or any(not start <= order_day(order) <= end for order in orders):
        raise ValueError("Pedido fuera del corte o del intervalo")
    return start


def make_loader(store, config_store, engine_factory, start, end, supplier, reader=None):
    def loader(order_id):
        config = config_store.load()
        if config["supplier_id"] != supplier:
            raise ValueError("El proveedor configurado ha cambiado")
        check_permission(store, config, [order_id])
        query = reader or configured_orders
        orders = query(engine_factory(), config, start, end, [order_id])
        if not orders:
            raise ValueError("Pedido fuera del alcance actual")
        prepare_selection(store, config, orders, start, end)
        return orders[0]
    return loader


def schedule_status(store, config, now=None):
    timestamp = time.time() if now is None else now
    with store.connect() as connection:
        row = connection.execute("SELECT * FROM schedule_state WHERE id=1").fetchone()
    status = dict(row) if row else {"last_started": None, "last_finished": None, "result": "Sin ejecuciones", "run_id": None}
    interval = float(config.get("auto_interval_hours", 6)) * 3600
    if status["result"] in {"Error", "Parcial", "Interrumpido"}:
        interval = max(interval, ERROR_BACKOFF)
    if not config.get("auto_enabled", False):
        due = None
    elif status["result"] == IN_PROGRESS:
        due = status.get("next_run")
    elif status["last_finished"] is not None:
        due = status["last_finished"] + interval
    else:
        due = timestamp
    status["next_run"] = due
    return status


class KeringScheduler:
    def __init__(self, store, config_store, engine_factory, portal_factory=KeringPortal, clock_fn=time.time):
        self.store = store
        self.config_store = config_store
        self.engine_factory = engine_factory
        self.portal_factory = portal_factory
        self.clock = clock_fn

    def write_state(self, started, finished, due, result, run_id):
        with self.store.connect() as connection:
            connection.execute("""INSERT INTO schedule_state VALUES(1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET last_started=excluded.last_started,
                last_finished=excluded.last_finished,next_run=excluded.next_run,
                result=excluded.result,run_id=excluded.run_id""", (started, finished, due, result, run_id))

    def tick(self):
        config = self.config_store.load()
        now = self.clock()
        status = schedule_status(self.store, config, now)
        if not config.get("auto_enabled", False):
            return {**status, "next_run": None}
        try:
            with batch_lock(self.store):
                config = self.config_store.load()
                now = self.clock()
                status = schedule_status(self.store, config, now)
                if not config.get("auto_enabled", False):
                    return status
                return self.tick_locked(config, now, status)
        except Timeout:
            return {**status, "result": "Esperando lote activo"}

    def tick_locked(self, config, now, status):
        self.store.recover_abandoned(now)
        if status["result"] == IN_PROGRESS:
            due = now + max(float(config["auto_interval_hours"]) * 3600, ERROR_BACKOFF)
            self.write_state(status["last_started"], now, due, "Interrumpido", status["run_id"])
            return schedule_status(self.store, config, now)
        if status["next_run"] is not None and now < status["next_run"]:
            return status
        start = date.fromisoformat(config["cutoff_date"])
        end = datetime.fromtimestamp(now, MADRID).date()
        run_id = self.store.create_run([], "programador", config["supplier_id"], start, end, AUTO_ORIGIN)
        self.write_state(now, None, now + float(config["auto_interval_hours"]) * 3600, IN_PROGRESS, run_id)
        result = "Error"
        try:
            result = self.process_pending(config, now, run_id)
        except Exception as error:
            LOGGER.error("kering_scheduler stage=preparacion run_id=%s code=fallo_programador exception_type=%s",
                         run_id, type(error).__name__)
            result = "Error"
            with self.store.connect() as connection:
                connection.execute("UPDATE runs SET status='Error',ended=? WHERE id=?", (self.clock(), run_id))
        finally:
            finished = self.clock()
            interval = float(config["auto_interval_hours"]) * 3600
            if result in {"Error", "Parcial", "Interrumpido"}:
                interval = max(interval, ERROR_BACKOFF)
            due = finished + interval if config.get("auto_enabled") else None
            self.write_state(now, finished, due, result, run_id)
        return schedule_status(self.store, self.config_store.load(), self.clock())

    def process_pending(self, config, now, run_id):
        start = date.fromisoformat(config["cutoff_date"])
        end = datetime.fromtimestamp(now, MADRID).date()
        ids = config.get("test_order_ids", []) if config.get("test_mode", True) else None
        if ids == []:
            execute_run(self.store, run_id, lambda _: None, None)
            return "Sin pedidos autorizados"
        check_permission(self.store, config, ids or [0])
        try:
            orders = configured_orders(self.engine_factory(), config, start, end, ids)
        except Exception as error:
            LOGGER.error("kering_scheduler stage=lectura_pedidos run_id=%s exception_type=%s",
                         run_id, type(error).__name__)
            raise
        orders = [order for order in orders if start <= order_day(order) <= end and (ids is None or order["id"] in ids)]
        history = {}
        for attempt in self.store.history():
            history.setdefault(attempt["order_id"], []).append(attempt)
        views_cache = {}
        pending = []
        decisions = {}
        for order in orders:
            attempts = history.get(order["id"], [])
            status = purchase_status(self.store, order, attempts[0] if attempts else None, views_cache)
            decision = automatic_decision(order, status, attempts, config, now)
            decisions[decision] = decisions.get(decision, 0) + 1
            if decision == "elegible":
                pending.append(order)
        if decisions.get("revision_limite_tecnico") or decisions.get("revision_parcial"):
            LOGGER.warning("kering_scheduler run_id=%s decisions=%s", run_id, json.dumps(decisions, sort_keys=True))
        self.store.add_orders(run_id, pending)
        portal = None
        try:
            portal = self.portal_factory(config) if pending else None
            loader = make_loader(self.store, self.config_store, self.engine_factory, start, end, config["supplier_id"])
            execute_run(self.store, run_id, loader, portal)
        except Exception as error:
            LOGGER.error("kering_scheduler stage=procesamiento run_id=%s code=fallo_programador exception_type=%s",
                         run_id, type(error).__name__)
            with self.store.connect() as connection:
                connection.execute("UPDATE attempts SET status='Error',error='fallo_programador' WHERE run_id=?", (run_id,))
                connection.execute("UPDATE runs SET status='Error',ended=? WHERE id=?", (self.clock(), run_id))
            return "Error"
        finally:
            close = getattr(portal, "close", None)
            if close:
                close()
        if not pending:
            return "Sin pendientes"
        with self.store.connect() as connection:
            run = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if run["status"] == "Interrumpido":
            return "Interrumpido"
        outcomes = [row["status"] for row in self.store.history() if row["run_id"] == run_id]
        if all(value == "Completo" for value in outcomes):
            result = "Completado"
        elif all(value == "Error" for value in outcomes):
            result = "Error"
        else:
            result = "Parcial"
        return result


def db_engine():
    config = load_db_config()
    return create_engine(URL.create("postgresql+psycopg2", username=config.user, password=config.password,
                                    host=config.host, port=config.port, database=config.database))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Programador Kering")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    stop = Event()
    for signal_id in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_id, lambda *_: stop.set())
    engines = []
    def engine_factory():
        if not engines:
            engines.append(db_engine())
        return engines[0]
    scheduler = KeringScheduler(ImageStore(data_root()), ConfigStore(data_root()), engine_factory)
    last_event = None
    try:
        while not stop.is_set():
            delay = 60
            try:
                status = scheduler.tick()
                event = {"result": status["result"], "run_id": status.get("run_id")}
                if event != last_event:
                    print(json.dumps(event), flush=True)
                    last_event = event
                if status["next_run"] is not None:
                    delay = max(1, min(60, status["next_run"] - time.time()))
                if status["result"] == "Esperando lote activo":
                    delay = 60
            except Exception:
                print("Error de configuracion o almacenamiento Kering; detalles omitidos", flush=True)
                delay = ERROR_BACKOFF
            if args.once:
                return 0
            stop.wait(delay)
    finally:
        for engine in engines:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())