"""One persistent, explicitly activated 72-hour experiment, not a global scheduler."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import signal
import subprocess
import threading
import time
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from price_comparator import PriceComparatorStore, normalize_gtin
from price_connectors import ConnectorError

LOG = logging.getLogger(__name__)
MANIFEST_PATH = Path(__file__).with_name("price_trial_manifest.json")
HEARTBEAT_KEY = "price_trial_worker_heartbeat"


class TrialDenied(ConnectorError):
    pass


@dataclass(frozen=True)
class Execution:
    database: str
    cycle: int
    competitor_id: str = ""
    ean: str = ""


_execution: ContextVar[Execution | None] = ContextVar("price_trial_execution", default=None)


def utcnow():
    return datetime.now(timezone.utc)


def manifest() -> dict:
    result = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    eans = result["eans"]
    if len(eans) != 100 or len(set(eans)) != 100 or any(normalize_gtin(e) != e for e in eans):
        raise ValueError("El manifiesto debe contener los 100 EAN normalizados, sin duplicados")
    if len(result["competitors"]) != 6 or set(result["competitors"].values()) != {
            "Óptica Óptima", "Amevista", "Visio Factory", "Edel-Optics", "Domanci", "ConGafasDeSol"}:
        raise ValueError("Competidores fuera del alcance autorizado")
    result["pairs"] = [{"competitor_id": cid, "ean": ean}
                       for cid in result["competitors"] for ean in eans]
    return result


def trial_row(store):
    with store._connect() as con:
        row = con.execute("SELECT * FROM price_trial WHERE id=1").fetchone()
    return dict(row) if row else None


def active_execution(store) -> Execution | None:
    execution = _execution.get()
    return execution if execution and execution.database == str(store.database_path) else None


def pair_reason(store, cid: str, ean: str | None, now: datetime) -> str:
    """Ownership survives expiry: jobs created by the trial cannot leak into other schedulers."""
    row = trial_row(store)
    if row is None:
        return ""
    frozen = json.loads(row["manifest"])
    owned = cid in frozen["competitors"] and (not ean or ean in frozen["eans"])
    execution = active_execution(store)
    if execution is None:
        return "Par reservado al piloto de 72 h; no se ejecuta desde otro disparador" if owned else ""
    if (row["status"] != "active" or now < datetime.fromisoformat(row["started_at"])
            or now >= datetime.fromisoformat(row["expires_at"])):
        return "Piloto finalizado o caducado: no se inician nuevas consultas"
    if cid != execution.competitor_id or (ean and ean != execution.ean) or not owned:
        return "Petición fuera del par congelado de esta ejecución"
    return ""


def require_pair(store, cid: str, ean: str | None, now: datetime) -> None:
    reason = pair_reason(store, cid, ean, now)
    if reason:
        raise TrialDenied(reason)


def select_eans(store, cid: str, eans: list[str] | None, now: datetime) -> list[str] | None:
    if trial_row(store) is None:
        return eans
    if eans is None:
        with store._connect() as con:
            eans = [r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")]
    return [ean for ean in eans if not pair_reason(store, cid, ean, now)]


def require_sync(store, eans: list[str], now: datetime) -> None:
    row = trial_row(store)
    if row is None:
        return
    frozen = set(json.loads(row["manifest"])["eans"])
    execution = active_execution(store)
    if execution:
        if (row["status"] != "active" or now < datetime.fromisoformat(row["started_at"])
                or now >= datetime.fromisoformat(row["expires_at"])
                or execution.competitor_id or set(eans) != frozen or len(eans) != len(frozen)):
            raise TrialDenied("Sincronización fuera de alcance o ventana del piloto")
    elif frozen.intersection(eans):
        raise TrialDenied("EAN reservados al sincronizador filtrado del piloto de 72 h")


def budget_limits(store, cid: str, budget: dict) -> dict:
    execution = active_execution(store)
    if not execution:
        return budget
    row = trial_row(store)
    limits = json.loads(row["limits"]).get(cid)
    if limits is None:
        raise TrialDenied("Competidor fuera del presupuesto congelado")
    return {**budget, "daily_limit": min(budget["daily_limit"], limits["daily_limit"], 200),
            "per_minute_limit": min(budget["per_minute_limit"], limits["per_minute_limit"], 10),
            "effective_pause": max(budget["effective_pause"], limits["effective_pause"], 3.0)}


class PriceTrial:
    def __init__(self, store, *, clock=utcnow, batch=None, sync=None):
        self.store, self.clock = store, clock
        self._batch, self._sync = batch, sync

    def lock(self):
        return FileLock(f"{self.store.database_path}.trial.lock", timeout=0)

    def heartbeat(self):
        with self.store._connect() as con, con:
            con.execute("""INSERT INTO comparator_settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                        (HEARTBEAT_KEY, self.clock().isoformat()))
            con.execute("UPDATE price_trial SET heartbeat_at=? WHERE id=1", (self.clock().isoformat(),))

    def preflight(self) -> dict:
        from price_access import AccessControl
        frozen = manifest()
        with self.store._connect() as con:
            products = {r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")}
            shops = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM competitors WHERE active=1")}
            beat = con.execute("SELECT value FROM comparator_settings WHERE key=?", (HEARTBEAT_KEY,)).fetchone()
        if not set(frozen["eans"]) <= products:
            raise ValueError("Faltan EAN del manifiesto en la vigilancia activa; no se amplía silenciosamente")
        if any(shops.get(cid) != name for cid, name in frozen["competitors"].items()):
            raise ValueError("Los seis competidores del manifiesto no coinciden con el inventario activo")
        if not beat or not timedelta(0) <= self.clock() - datetime.fromisoformat(beat[0]) < timedelta(minutes=2):
            raise ValueError("No hay heartbeat reciente del trabajador persistente; no se activa")
        access = AccessControl(self.store, clock=self.clock)
        limits = {cid: access.budget("competitor", cid) for cid in frozen["competitors"]}
        return {"manifest": frozen, "limits": limits}

    def activate(self, *, actor: str, build_commit: str):
        if not actor.strip() or not build_commit.strip():
            raise ValueError("Actor y commit desplegado requeridos")
        with self.lock(), ExitStack() as stack:
            prepared = self.preflight()
            for cid in sorted(prepared["manifest"]["competitors"]):
                stack.enter_context(FileLock(f"{self.store.database_path}.competitor-{cid}.lock", timeout=0))
            stack.enter_context(FileLock(f"{self.store.database_path}.vytria-sync.lock", timeout=0))
            now = self.clock()
            with self.store._connect() as con, con:
                if con.execute("SELECT 1 FROM price_trial").fetchone():
                    raise ValueError("Este piloto ya tiene ventana persistida; no se reinicia ni se prolonga")
                con.execute("""INSERT INTO price_trial
                    (id,status,started_at,expires_at,actor,build_commit,manifest,limits)
                    VALUES(1,'active',?,?,?,?,?,?)""",
                            (now.isoformat(), (now + timedelta(hours=72)).isoformat(), actor.strip(),
                             build_commit, json.dumps(prepared["manifest"], ensure_ascii=False),
                             json.dumps(prepared["limits"])))
                for cycle in range(3):
                    con.execute("INSERT INTO price_trial_cycles(cycle,started_at) VALUES(?,?)",
                                (cycle, (now + timedelta(hours=24 * cycle)).isoformat()))
                    con.executemany("""INSERT INTO price_trial_pairs(cycle,competitor_id,ean) VALUES(?,?,?)""",
                                    [(cycle, p["competitor_id"], p["ean"]) for p in prepared["manifest"]["pairs"]])
        return self.report()

    def stop(self, *, actor: str):
        if not actor.strip():
            raise ValueError("Indique quién detiene el piloto")
        with self.store._connect() as con, con:
            con.execute("""UPDATE price_trial SET status='stopped',stopped_at=?,error=?
                WHERE id=1 AND status='active'""", (self.clock().isoformat(), f"Detenido por {actor}"))

    def _expire(self):
        now = self.clock().isoformat()
        with self.store._connect() as con, con:
            con.execute("""UPDATE price_trial SET status='expired',stopped_at=expires_at
                WHERE status='active' AND expires_at<=?""", (now,))
            pending = con.execute("""SELECT cycle,competitor_id,ean,receipt FROM price_trial_pairs
                WHERE state='pending' AND EXISTS(SELECT 1 FROM price_trial WHERE status<>'active')""").fetchall()
            for pair in pending:
                receipt = json.loads(pair["receipt"])
                receipt["closure_reason"] = "Piloto finalizado; sin consultas nuevas"
                con.execute("""UPDATE price_trial_pairs SET state='done',finished_at=?,receipt=?
                    WHERE cycle=? AND competitor_id=? AND ean=?""",
                            (now, json.dumps(receipt), pair["cycle"], pair["competitor_id"], pair["ean"]))

    @contextmanager
    def execution(self, cycle, cid="", ean=""):
        token = _execution.set(Execution(str(self.store.database_path), cycle, cid, ean))
        try:
            yield
        finally:
            _execution.reset(token)

    def tick(self, *, max_pairs=20, deadline_seconds=240):
        """One process at a time; each pair is reserved durably before calling existing workers."""
        try:
            with self.lock():
                self.heartbeat()
                self._close_interrupted()
                self._expire()
                row = trial_row(self.store)
                if not row or row["status"] != "active":
                    return
                if self.clock() < datetime.fromisoformat(row["started_at"]):
                    return
                frozen = json.loads(row["manifest"])
                cycle = min(2, int((self.clock() - datetime.fromisoformat(row["started_at"])).total_seconds() // 86400))
                now = self.clock().isoformat()
                with self.store._connect() as con, con:
                    con.execute("INSERT OR IGNORE INTO price_trial_cycles(cycle,started_at) VALUES(?,?)", (cycle, now))
                    con.executemany("""INSERT OR IGNORE INTO price_trial_pairs(cycle,competitor_id,ean)
                        VALUES(?,?,?)""", [(cycle, p["competitor_id"], p["ean"]) for p in frozen["pairs"]])
                    sync_state = con.execute("SELECT sync_state FROM price_trial_cycles WHERE cycle=?", (cycle,)).fetchone()[0]
                if sync_state == "pending":
                    self._synchronize(cycle, frozen["eans"])
                elif sync_state == "running":
                    self._save_sync(cycle, {"status": "interrupted", "error": "Sincronización interrumpida; no se duplica"})
                from price_batch import WatchBatch
                batch = self._batch or WatchBatch(self.store)
                started = time.monotonic()
                with self.store._connect() as con:
                    pairs = con.execute("""SELECT * FROM price_trial_pairs
                        WHERE cycle=? AND state='pending' ORDER BY competitor_id,ean LIMIT ?""",
                                        (cycle, max_pairs)).fetchall()
                for pair in pairs:
                    self.heartbeat()
                    self._expire()
                    if trial_row(self.store)["status"] != "active" or time.monotonic() - started >= deadline_seconds:
                        break
                    cid, ean = pair["competitor_id"], pair["ean"]
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE price_trial_pairs SET state='running',started_at=?
                            WHERE cycle=? AND competitor_id=? AND ean=?""", (self.clock().isoformat(), cycle, cid, ean))
                    with self.execution(cycle, cid, ean):
                        before = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                        with self.store._connect() as con:
                            first_request = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]
                        result = batch.execute([ean], [cid], approved=True, sync_vytria=False, max_jobs=1)
                        with self.store._connect() as con:
                            result["http_receipts"] = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                                WHERE competitor_id=? AND id>? ORDER BY id""", (cid, first_request))]
                    result["inventory_before"] = before
                    prior = json.loads(pair["receipt"])
                    segments = prior.get("segments", [])
                    segments.append(result)
                    continuation = any("Plazo de " in run.get(key, "")
                                       for run in result["runs"]
                                       for key in ("location_deferred", "monitor_deferred"))
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE price_trial_pairs SET state=?,finished_at=?,receipt=?
                            WHERE cycle=? AND competitor_id=? AND ean=?""",
                                    ("pending" if continuation else "done", self.clock().isoformat(),
                                     json.dumps({"segments": segments}, ensure_ascii=False), cycle, cid, ean))
                self._expire()
        except Timeout:
            return
        except Exception as exc:
            LOG.exception("Price trial stopped after a shared-flow failure")
            with self.store._connect() as con, con:
                con.execute("""UPDATE price_trial SET status='failed',stopped_at=?,error=?
                    WHERE status='active'""", (self.clock().isoformat(), f"{type(exc).__name__}: {exc}"))
            raise

    def _close_interrupted(self):
        # Called only under the worker lock: no live trial can own these reservations.
        with self.store._connect() as con, con:
            for pair in con.execute("SELECT * FROM price_trial_pairs WHERE state='running'").fetchall():
                receipt = json.loads(pair["receipt"])
                receipt["closure_reason"] = (
                    "Trabajador interrumpido: no se repite una reserva con resultado incierto")
                receipt["interrupted_http_receipts"] = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                    WHERE competitor_id=? AND ean=? AND reserved_at>=? ORDER BY id""",
                    (pair["competitor_id"], pair["ean"], pair["started_at"]))]
                con.execute("""UPDATE price_trial_pairs SET state='done',finished_at=?,receipt=?
                    WHERE cycle=? AND competitor_id=? AND ean=?""",
                            (self.clock().isoformat(), json.dumps(receipt), pair["cycle"],
                             pair["competitor_id"], pair["ean"]))

    def _synchronize(self, cycle, eans):
        from vytria_price_sync import VytriaPriceSync
        with self.store._connect() as con, con:
            con.execute("UPDATE price_trial_cycles SET sync_state='running' WHERE cycle=?", (cycle,))
        with self.execution(cycle):
            result = (self._sync or VytriaPriceSync(self.store)).run(trigger="trial:72h", eans=eans)
        self._save_sync(cycle, asdict(result))

    def _save_sync(self, cycle, receipt):
        with self.store._connect() as con, con:
            con.execute("UPDATE price_trial_cycles SET sync_state='done',sync_receipt=? WHERE cycle=?",
                        (json.dumps(receipt, ensure_ascii=False), cycle))

    def report(self):
        self._expire()
        row = trial_row(self.store)
        if not row:
            return None
        row["manifest"] = json.loads(row["manifest"])
        row["manifest_sha256"] = hashlib.sha256(
            json.dumps(row["manifest"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row["limits"] = json.loads(row["limits"])
        for field in ("started_at", "expires_at", "stopped_at"):
            row[field + "_madrid"] = (datetime.fromisoformat(row[field]).astimezone(ZoneInfo("Europe/Madrid")).isoformat()
                                      if row[field] else None)
        with self.store._connect() as con:
            row["cycles"] = [{**dict(r), "sync_receipt": json.loads(r["sync_receipt"])}
                             for r in con.execute("SELECT * FROM price_trial_cycles ORDER BY cycle")]
            row["pairs"] = [{**dict(r), "receipt": json.loads(r["receipt"])}
                            for r in con.execute("SELECT * FROM price_trial_pairs ORDER BY cycle,competitor_id,ean")]
        row["summary"] = {}
        for saved in row["pairs"]:
            scheduled = datetime.fromisoformat(row["cycles"][saved["cycle"]]["started_at"])
            if saved["state"] == "pending":
                saved["pending_reason"] = (
                    "Ciclo todavía no iniciado" if self.clock() < scheduled else
                    "Ciclo anterior incompleto; la cola se vuelve a evaluar en el ciclo actual"
                    if self.clock() >= scheduled + timedelta(hours=24) else
                    "Pendiente del siguiente turno del trabajador; límites de tiempo y pares por turno")
            key = f"{saved['cycle']}:{saved['competitor_id']}"
            totals = row["summary"].setdefault(key, {
                "processed_pairs": 0, "http": 0, "reserved_http": 0, "unconfirmed_http": 0,
                "new_fiches": 0, "reused_fiches": 0,
                "comparable": 0, "out_of_stock": 0, "review": 0, "not_located": 0, "deferred": 0})
            segments = saved["receipt"].get("segments", [])
            results = [p for segment in segments for p in segment["pairs"]]
            outcomes = [o for p in results for o in p["outcomes"]]
            totals["processed_pairs"] += int(any(p["processed"] for p in results))
            receipts = {r["id"]: r for s in segments for r in s.get("http_receipts", [])}
            receipts.update({r["id"]: r for r in saved["receipt"].get("interrupted_http_receipts", [])})
            totals["http"] += sum(r["result"].startswith(("HTTP ", "error:")) for r in receipts.values())
            totals["reserved_http"] += len(receipts)
            totals["unconfirmed_http"] += sum(r["result"] in {"", "reserved"} for r in receipts.values())
            monitored = [o for o in outcomes if o.get("sample")]
            if monitored:
                latest = monitored[-1]
                sample = latest["sample"]
                page = sample["page"]
                totals["comparable"] += int(latest["state"] == "done" and not sample["gap"]
                                            and page["includes_tax"] is True and not page["stock_conflict"]
                                            and page["availability"] == "available")
                totals["out_of_stock"] += int(page["availability"] == "unavailable")
            initially_located = bool(segments and segments[0]["inventory_before"]["located"])
            located = initially_located or any(o["state"] == "done" and "candidate_plan" in o for o in outcomes)
            totals["reused_fiches"] += int(initially_located and any(
                p["processed"] or p["category"] == "reused" for p in results))
            totals["new_fiches"] += int(located and not initially_located)
            totals["review"] += int(any(o["state"] == "review" for o in outcomes)
                                    or bool(segments and "Revisión" in segments[-1]["inventory_before"]["action"]))
            totals["not_located"] += int(any(o["state"] in {"not_found", "unavailable"} for o in outcomes)
                                        and not located)
            totals["deferred"] += int(not results or results[-1]["category"] == "not_executed")
        return row


def serve(command):
    """Child worker belongs only to the app container, never the other image services."""
    from price_comparator import resolve_database_path
    child = subprocess.Popen(command)
    stopped = threading.Event()

    def stop(signum, frame):
        stopped.set()
        child.send_signal(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def worker():
        while not stopped.is_set() and child.poll() is None:
            try:
                if resolve_database_path().is_file():
                    store = PriceComparatorStore()
                    PriceTrial(store).tick()
                    row = trial_row(store)
                    if row and row["status"] != "active":
                        return
            except Exception:
                LOG.exception("Price trial worker failed; no automatic reactivation")
            stopped.wait(30)

    thread = threading.Thread(target=worker, name="bounded-price-trial", daemon=True)
    thread.start()
    try:
        return child.wait()
    finally:
        stopped.set()
        thread.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.serve:
        parser.error("--serve requires the app command")
    raise SystemExit(serve(args.serve))
