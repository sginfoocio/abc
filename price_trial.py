"""Persistent, explicitly activated price-watch pilot and continuity workflows."""
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
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from price_comparator import PriceComparatorStore, normalize_gtin
from price_connectors import ConnectorError

LOG = logging.getLogger(__name__)
MANIFEST_PATH = Path(__file__).with_name("price_trial_manifest.json")
CONTINUITY_CONFIG_PATH = Path(__file__).with_name("price_watch_continuity.json")
HEARTBEAT_KEY = "price_trial_worker_heartbeat"
INITIAL_SYNC_FAILURE = "TrialDenied: EAN reservados al sincronizador filtrado del piloto de 72 h"
PASSIVE_CLOSURE = {"closure_reason": "Piloto finalizado; sin consultas nuevas"}


class TrialDenied(ConnectorError):
    pass


@dataclass(frozen=True)
class Execution:
    database: str
    cycle: int
    competitor_id: str = ""
    ean: str = ""
    program: str = "trial"
    program_id: int = 1


_execution: ContextVar[Execution | None] = ContextVar("price_trial_execution", default=None)


def utcnow():
    return datetime.now(timezone.utc)


def _utc_datetime(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


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


def continuity_config() -> dict:
    config = json.loads(CONTINUITY_CONFIG_PATH.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    if config.get("manifest_sha256") != expected_hash:
        raise ValueError("La continuidad no referencia el manifiesto original sin cambios")
    if (config.get("interval_hours") != 24
            or config.get("offer_check_interval_hours") != 24
            or config.get("daily_http_limit") != 200
            or config.get("per_minute_limit") != 10
            or config.get("minimum_pause_seconds") != 3
            or config.get("start_policy") != "persist_current_time_at_activation"
            or config.get("responsible_policy") != "required_operator_at_activation"
            or config.get("require_explicit_activation") is not True
            or config.get("controls") != ["pause", "resume", "stop"]):
        raise ValueError("Los controles de continuidad no coinciden con los límites aprobados")
    config["manifest"] = manifest()
    return config


def trial_row(store):
    with store._connect() as con:
        row = con.execute("SELECT * FROM price_trial WHERE id=1").fetchone()
    return dict(row) if row else None


def continuity_row(store, continuity_id: int | None = None):
    with store._connect() as con:
        row = (con.execute("SELECT * FROM price_continuity WHERE id=?", (continuity_id,)).fetchone()
               if continuity_id is not None else
               con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone())
    return dict(row) if row else None


def _is_robots_receipt(receipt: dict) -> bool:
    return urlsplit(receipt.get("url", "")).path.lower().rstrip("/") == "/robots.txt"


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
    if execution and execution.program == "continuity":
        continuity = continuity_row(store, execution.program_id)
        if row["status"] == "active":
            return "La vigilancia de continuidad no puede solaparse con el piloto original"
        if not continuity or continuity["status"] != "active":
            return "La vigilancia de continuidad no está activa"
        selected = json.loads(continuity["manifest"])
        if (cid not in selected["competitors"] or (ean and ean not in selected["eans"])
                or cid != execution.competitor_id or (ean and ean != execution.ean)):
            return "Petición fuera del par congelado de continuidad"
        if now < datetime.fromisoformat(continuity["started_at"]):
            return "La vigilancia de continuidad todavía no ha comenzado"
        return ""
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
    if execution and execution.program == "continuity":
        continuity = continuity_row(store, execution.program_id)
        if (not continuity or continuity["status"] != "active"
                or continuity["started_at"] is None
                or now < datetime.fromisoformat(continuity["started_at"])
                or execution.competitor_id or execution.ean
                or set(eans) != set(json.loads(continuity["manifest"])["eans"])
                or len(eans) != len(set(eans))):
            raise TrialDenied("Sincronización fuera del manifiesto activo de continuidad")
        return
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
    if execution.program == "continuity":
        row = continuity_row(store, execution.program_id)
        if not row or row["status"] != "active":
            raise TrialDenied("La vigilancia de continuidad no está activa")
    else:
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

    def continuity_lock(self):
        return FileLock(f"{self.store.database_path}.continuity.lock", timeout=0)

    def heartbeat(self):
        now = self.clock().isoformat()
        with self.store._connect() as con, con:
            con.execute("""INSERT INTO comparator_settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                        (HEARTBEAT_KEY, now))
            con.execute("UPDATE price_trial SET heartbeat_at=? WHERE id=1", (now,))
            con.execute("""UPDATE price_continuity SET heartbeat_at=?
                WHERE status IN ('prepared','active','paused')""", (now,))

    def continuity_preflight(self) -> dict:
        from price_access import AccessControl
        config = continuity_config()
        frozen = config["manifest"]
        with self.store._connect() as con:
            products = {r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")}
            shops = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM competitors WHERE active=1")}
            beat = con.execute("SELECT value FROM comparator_settings WHERE key=?", (HEARTBEAT_KEY,)).fetchone()
            trial = con.execute("SELECT status FROM price_trial WHERE id=1").fetchone()
        if not set(frozen["eans"]) <= products:
            raise ValueError("Faltan EAN del manifiesto en la vigilancia activa; no se amplía silenciosamente")
        if any(shops.get(cid) != name for cid, name in frozen["competitors"].items()):
            raise ValueError("Los seis competidores del manifiesto no coinciden con el inventario activo")
        if trial is None or trial["status"] not in {"expired", "stopped"}:
            raise ValueError("La continuidad solo puede prepararse después del cierre del piloto original")
        if not beat or not timedelta(0) <= self.clock() - datetime.fromisoformat(beat[0]) < timedelta(minutes=2):
            raise ValueError("No hay heartbeat reciente del trabajador persistente; no se prepara")
        access = AccessControl(self.store, clock=self.clock)
        limits = {cid: access.budget("competitor", cid) for cid in frozen["competitors"]}
        return {"config": config, "manifest": frozen, "limits": limits}

    def prepare_continuity(self, *, actor: str) -> dict:
        if not actor.strip():
            raise ValueError("Indique quién prepara la continuidad")
        with self.continuity_lock():
            prepared = self.continuity_preflight()
            with self.store._connect() as con, con:
                row = con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    return self.continuity_report()
                now = self.clock().isoformat()
                con.execute("""INSERT INTO price_continuity
                    (status,prepared_at,actor,build_commit,manifest,limits,interval_hours)
                    VALUES('prepared',?,?,?,?,?,?)""",
                            (now, actor.strip(), "", json.dumps(prepared["manifest"], ensure_ascii=False),
                             json.dumps(prepared["limits"]), prepared["config"]["interval_hours"]))
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity','1','prepare','none','prepared',?,?,?)""",
                            (actor.strip(), json.dumps({"manifest_sha256": prepared["config"]["manifest_sha256"]}),
                             now))
        return self.continuity_report()

    def activate_continuity(self, *, actor: str, build_commit: str) -> dict:
        if not actor.strip() or len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
            raise ValueError("Responsable y commit desplegado válidos requeridos")
        with self.continuity_lock(), ExitStack() as stack:
            prepared = self.continuity_preflight()
            row = continuity_row(self.store)
            if not row or row["status"] != "prepared":
                raise ValueError("No existe una continuidad preparada y pendiente de aprobación")
            if json.loads(row["manifest"]) != prepared["manifest"]:
                raise ValueError("El manifiesto preparado difiere del manifiesto original")
            for cid in sorted(prepared["manifest"]["competitors"]):
                stack.enter_context(FileLock(f"{self.store.database_path}.competitor-{cid}.lock", timeout=0))
            stack.enter_context(FileLock(f"{self.store.database_path}.vytria-sync.lock", timeout=0))
            now = self.clock().isoformat()
            with self.store._connect() as con, con:
                changed = con.execute("""UPDATE price_continuity SET status='active',started_at=?,actor=?,
                    build_commit=?,limits=?,next_sync_at=?,stopped_at=NULL,error=''
                    WHERE id=? AND status='prepared'""",
                                      (now, actor.strip(), build_commit, json.dumps(prepared["limits"]),
                                       now, row["id"])).rowcount
                if not changed:
                    raise ValueError("La continuidad cambió antes de activarse")
                con.executemany("""INSERT INTO price_continuity_pairs
                    (continuity_id,competitor_id,ean) VALUES(?,?,?)""",
                                [(row["id"], p["competitor_id"], p["ean"])
                                 for p in prepared["manifest"]["pairs"]])
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_continuity',?,'activate','prepared','active',?,?,?)""",
                            (str(row["id"]), actor.strip(),
                             json.dumps({"build_commit": build_commit,
                                         "manifest_sha256": prepared["config"]["manifest_sha256"]}), now))
        return self.continuity_report()

    def _continuity_control(self, action: str, *, actor: str) -> dict:
        if not actor.strip():
            raise ValueError("Indique quién realiza el cambio")
        with self.store._connect() as con, con:
            row = con.execute("SELECT * FROM price_continuity ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                raise ValueError("No hay continuidad persistida")
            transitions = {"pause": {"active": "paused"}, "resume": {"paused": "active"},
                           "stop": {"prepared": "stopped", "active": "stopped", "paused": "stopped"}}
            target = transitions[action].get(row["status"])
            if target is None:
                raise ValueError(f"No se puede {action} una continuidad en estado {row['status']}")
            now = self.clock().isoformat()
            if action == "pause":
                changed = con.execute("""UPDATE price_continuity SET status=?,paused_at=?
                    WHERE id=? AND status=?""", (target, now, row["id"], row["status"])).rowcount
            elif action == "resume":
                changed = con.execute("""UPDATE price_continuity SET status=?,paused_at=NULL
                    WHERE id=? AND status=?""", (target, row["id"], row["status"])).rowcount
            else:
                changed = con.execute("""UPDATE price_continuity SET status=?,stopped_at=?
                    WHERE id=? AND status=?""", (target, now, row["id"], row["status"])).rowcount
            if not changed:
                raise ValueError("La continuidad cambió antes de registrar el control")
            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                VALUES('price_continuity',?,?,?,?,?,?,?)""",
                        (str(row["id"]), action, row["status"], target, actor.strip(), "", now))
        return self.continuity_report()

    def pause_continuity(self, *, actor: str) -> dict:
        return self._continuity_control("pause", actor=actor)

    def resume_continuity(self, *, actor: str) -> dict:
        return self._continuity_control("resume", actor=actor)

    def stop_continuity(self, *, actor: str) -> dict:
        return self._continuity_control("stop", actor=actor)

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

    def recovery_preflight(self):
        """Only the known pre-I/O startup failure can be resumed; uncertain work is never replayed."""
        row = trial_row(self.store)
        if not row or row["status"] != "failed" or row["error"] != INITIAL_SYNC_FAILURE:
            raise ValueError("Solo se recupera el fallo inicial conocido del contexto de sincronización")
        now = self.clock()
        if not datetime.fromisoformat(row["started_at"]) <= now < datetime.fromisoformat(row["expires_at"]):
            raise ValueError("La ventana original no está vigente; no se reinicia ni se amplía")
        prepared = self.preflight()
        frozen = json.loads(row["manifest"])
        if frozen != prepared["manifest"]:
            raise ValueError("El manifiesto desplegado difiere del alcance congelado")
        with self.store._connect() as con:
            cycles = [dict(r) for r in con.execute("SELECT * FROM price_trial_cycles ORDER BY cycle")]
            pairs = [dict(r) for r in con.execute("SELECT * FROM price_trial_pairs ORDER BY cycle,competitor_id,ean")]
            marks = ",".join("?" for _ in frozen["competitors"])
            requests = con.execute(f"""SELECT COUNT(*) FROM access_requests
                WHERE competitor_id IN ({marks}) AND reserved_at>=?""",
                                   (*frozen["competitors"], row["started_at"])).fetchone()[0]
            syncs = con.execute("""SELECT COUNT(*) FROM vytria_sync_runs
                WHERE trigger='trial:72h' AND started_at>=?""", (row["started_at"],)).fetchone()[0]
        if requests or syncs:
            raise ValueError("Hay peticiones o sincronizaciones previas: se requiere revisión; no se repiten")
        expected = {(cycle, pair["competitor_id"], pair["ean"])
                    for cycle in range(3) for pair in frozen["pairs"]}
        if {(p["cycle"], p["competitor_id"], p["ean"]) for p in pairs} != expected:
            raise ValueError("Los pares persistidos no coinciden con los tres ciclos congelados")
        if len(cycles) != 3 or [c["cycle"] for c in cycles] != [0, 1, 2]:
            raise ValueError("Ciclos persistidos inesperados")
        for cycle in cycles:
            scheduled = datetime.fromisoformat(row["started_at"]) + timedelta(hours=24 * cycle["cycle"])
            if (datetime.fromisoformat(cycle["started_at"]) != scheduled
                    or cycle["sync_state"] != ("running" if cycle["cycle"] == 0 else "pending")
                    or json.loads(cycle["sync_receipt"]) != {}):
                raise ValueError("Existe un ciclo previo o incierto: no se recupera automáticamente")
        for pair in pairs:
            receipt = json.loads(pair["receipt"])
            if (pair["started_at"] is not None or pair["state"] not in {"pending", "done"}
                    or receipt not in ({}, PASSIVE_CLOSURE)
                    or (pair["state"] == "done" and receipt != PASSIVE_CLOSURE)):
                raise ValueError("Existe un par iniciado o con recibo: no se repite trabajo incierto")
        return {"trial": row, "cycles": cycles, "pairs": pairs}

    def resume_initial_sync_failure(self, *, actor: str, build_commit: str):
        if not actor.strip() or len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
            raise ValueError("Responsable y commit desplegado válidos requeridos")
        with self.lock(), ExitStack() as stack:
            row = trial_row(self.store)
            if not row:
                raise ValueError("No hay piloto persistido")
            frozen = json.loads(row["manifest"])
            for cid in sorted(frozen["competitors"]):
                stack.enter_context(FileLock(f"{self.store.database_path}.competitor-{cid}.lock", timeout=0))
            stack.enter_context(FileLock(f"{self.store.database_path}.vytria-sync.lock", timeout=0))
            previous = self.recovery_preflight()
            if build_commit == previous["trial"]["build_commit"]:
                raise ValueError("Despliegue primero la corrección; no se reanuda con la compilación original")
            at = self.clock().isoformat()
            with self.store._connect() as con, con:
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('price_trial','1','resume_initial_sync_failure','failed','active',?,?,?)""",
                            (actor.strip(), json.dumps({"recovery_build_commit": build_commit,
                                                       "previous": previous}, ensure_ascii=False), at))
                con.execute("UPDATE price_trial_cycles SET sync_state='pending' WHERE cycle=0")
                con.execute("""UPDATE price_trial_pairs SET state='pending',finished_at=NULL,receipt='{}'""")
                con.execute("UPDATE price_trial SET status='active',stopped_at=NULL,error='' WHERE id=1")
        return self.report()

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
    def execution(self, cycle, cid="", ean="", *, program="trial", program_id=1):
        token = _execution.set(Execution(str(self.store.database_path), cycle, cid, ean, program, program_id))
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
                self._inherit_cycle_deadlines(cycle, self.clock())
                if sync_state == "pending":
                    self._synchronize(cycle, frozen["eans"])
                elif sync_state == "running":
                    self._save_sync(cycle, {"status": "interrupted", "error": "Sincronización interrumpida; no se duplica"})
                from price_batch import WatchBatch
                batch = self._batch or WatchBatch(self.store)
                started = time.monotonic()
                with self.store._connect() as con:
                    pairs = con.execute("""SELECT * FROM price_trial_pairs
                        WHERE cycle=? AND state='pending' AND (next_check_at IS NULL OR next_check_at<=?)
                        ORDER BY CASE WHEN EXISTS(SELECT 1 FROM competitor_product_urls u
                            WHERE u.competitor_id=price_trial_pairs.competitor_id
                            AND u.ean=price_trial_pairs.ean AND u.status='verified') THEN 0 ELSE 1 END,
                            next_check_at,competitor_id,ean LIMIT ?""", (cycle, now, max_pairs)).fetchall()
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
                        after = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                        with self.store._connect() as con:
                            result["http_receipts"] = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                                WHERE competitor_id=? AND ean=? AND id>? ORDER BY id""",
                                                                                    (cid, ean, first_request))]
                    result["inventory_before"] = before
                    result["inventory_after"] = after
                    prior = json.loads(pair["receipt"])
                    segments = prior.get("segments", [])
                    segments.append(result)
                    next_check_at = (after.get("next_check_at") or after.get("access_retry_at")
                                     or after.get("not_before") or after.get("valid_until"))
                    try:
                        next_check = datetime.fromisoformat(next_check_at) if next_check_at else None
                    except (TypeError, ValueError):
                        next_check = None
                    if after.get("located") and after.get("last_ok_at"):
                        try:
                            interval = min(24.0, float(after.get("interval_hours") or 24))
                            verified_due = _utc_datetime(after["last_ok_at"]) + timedelta(hours=interval)
                            next_check = min(_utc_datetime(next_check), verified_due) if next_check else verified_due
                        except (TypeError, ValueError):
                            LOG.warning("No se pudo interpretar la vigencia verificada para %s/%s", cid, ean)
                    reason = after.get("defer_reason") or ""
                    if not reason and after.get("action") == "Reutilizar precio vigente":
                        reason = "Oferta verificada reutilizada; se reevalúa al vencer su vigencia."
                    time_limited = any("plazo" in str(run.get(field, "")).lower()
                                       for run in result.get("runs", [])
                                       for field in ("location_deferred", "monitor_deferred"))
                    retry_without_io = (time_limited and not result["http_receipts"]
                                        and not any(item.get("processed") for item in result.get("pairs", [])))
                    if retry_without_io:
                        reason = reason or "Plazo del turno agotado sin consultas; se retoma en el siguiente turno."
                        next_check = self.clock()
                    continuation = bool(next_check and next_check > self.clock()) or retry_without_io
                    with self.store._connect() as con, con:
                        con.execute("""UPDATE price_trial_pairs SET state=?,finished_at=?,next_check_at=?,
                            defer_reason=?,receipt=?
                            WHERE cycle=? AND competitor_id=? AND ean=?""",
                                    ("pending" if continuation else "done", self.clock().isoformat(),
                                     next_check.isoformat() if continuation else None, reason,
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

    def _inherit_cycle_deadlines(self, cycle: int, now: datetime) -> None:
        if cycle == 0:
            return
        with self.store._connect() as con, con:
            previous = con.execute("""SELECT competitor_id,ean,next_check_at,defer_reason
                FROM price_trial_pairs WHERE cycle=? AND state='pending' AND next_check_at IS NOT NULL""",
                                   (cycle - 1,)).fetchall()
            for prior in previous:
                try:
                    prior_due = _utc_datetime(prior["next_check_at"])
                except (TypeError, ValueError):
                    LOG.warning("Fecha siguiente inválida en ciclo %s para %s/%s",
                                cycle - 1, prior["competitor_id"], prior["ean"])
                    continue
                if prior_due <= now:
                    continue
                current = con.execute("""SELECT state,started_at,next_check_at FROM price_trial_pairs
                    WHERE cycle=? AND competitor_id=? AND ean=?""",
                                      (cycle, prior["competitor_id"], prior["ean"])).fetchone()
                if not current or current["state"] != "pending" or current["started_at"] is not None:
                    continue
                try:
                    current_due = _utc_datetime(current["next_check_at"]) if current["next_check_at"] else None
                except (TypeError, ValueError):
                    current_due = None
                if current_due is None or current_due < prior_due:
                    con.execute("""UPDATE price_trial_pairs SET next_check_at=?,defer_reason=?
                        WHERE cycle=? AND competitor_id=? AND ean=? AND state='pending' AND started_at IS NULL""",
                                (prior_due.isoformat(), prior["defer_reason"], cycle,
                                 prior["competitor_id"], prior["ean"]))

    def _close_continuity_interrupted(self, continuity_id: int) -> None:
        now = self.clock().isoformat()
        with self.store._connect() as con, con:
            rows = con.execute("""SELECT * FROM price_continuity_pairs
                WHERE continuity_id=? AND state='running'""", (continuity_id,)).fetchall()
            for pair in rows:
                receipts = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                    WHERE competitor_id=? AND ean=? AND reserved_at>=? ORDER BY id""",
                    (pair["competitor_id"], pair["ean"], pair["started_at"]))]
                reason = "Trabajador interrumpido; no se repite una reserva incierta"
                receipt = {"http_receipts": receipts, "closure_reason": reason}
                con.execute("""INSERT INTO price_continuity_checks
                    (continuity_id,competitor_id,ean,started_at,finished_at,state,category,requests,
                     http_commercial,http_robots,unconfirmed_http,defer_reason,receipt)
                    VALUES(?,?,?,?,?,'review','interrupted',?,?,?,?,?,?)""",
                            (continuity_id, pair["competitor_id"], pair["ean"], pair["started_at"], now,
                             len(receipts), sum(not _is_robots_receipt(r) for r in receipts),
                             sum(_is_robots_receipt(r) for r in receipts),
                             sum(r["result"] in {"", "reserved"} for r in receipts), reason,
                             json.dumps(receipt, ensure_ascii=False)))
                con.execute("""UPDATE price_continuity_pairs SET state='review',checked_at=?,next_check_at=NULL,
                    defer_reason=? WHERE id=?""", (now, reason, pair["id"]))

    def _run_continuity_sync(self, row: dict) -> None:
        next_sync = self.clock() + timedelta(hours=row["interval_hours"])
        with self.store._connect() as con, con:
            con.execute("UPDATE price_continuity SET next_sync_at=?,sync_receipt=? WHERE id=?",
                        (next_sync.isoformat(), json.dumps({"status": "running"}), row["id"]))
        try:
            from vytria_price_sync import VytriaPriceSync
            frozen = json.loads(row["manifest"])
            with self.execution(0, program="continuity", program_id=row["id"]):
                result = (self._sync or VytriaPriceSync(self.store)).run(
                    trigger="price-continuity", eans=frozen["eans"])
            receipt = asdict(result)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self.store._connect() as con, con:
                con.execute("""UPDATE price_continuity SET next_sync_at=?,error=?,sync_receipt=?
                    WHERE id=?""",
                            ((self.clock() + timedelta(hours=1)).isoformat(), message,
                             json.dumps({"status": "failed", "error": message}, ensure_ascii=False), row["id"]))
            raise
        with self.store._connect() as con, con:
            con.execute("UPDATE price_continuity SET sync_receipt=?,error='' WHERE id=?",
                        (json.dumps(receipt, ensure_ascii=False), row["id"]))

    def continuity_tick(self, *, max_pairs: int = 20, deadline_seconds: int = 240) -> None:
        try:
            with self.continuity_lock():
                self.heartbeat()
                row = continuity_row(self.store)
                if not row or row["status"] != "active":
                    return
                self._close_continuity_interrupted(row["id"])
                now = self.clock()
                if now < datetime.fromisoformat(row["started_at"]):
                    return
                if row["next_sync_at"] and datetime.fromisoformat(row["next_sync_at"]) <= now:
                    self._run_continuity_sync(row)
                from price_batch import WatchBatch
                batch = self._batch or WatchBatch(self.store)
                now_text = self.clock().isoformat()
                with self.store._connect() as con:
                    pairs = con.execute("""SELECT p.* FROM price_continuity_pairs p
                        WHERE p.continuity_id=? AND p.state='pending'
                          AND (p.next_check_at IS NULL OR p.next_check_at<=?)
                        ORDER BY CASE WHEN EXISTS(SELECT 1 FROM competitor_product_urls u
                            WHERE u.competitor_id=p.competitor_id AND u.ean=p.ean AND u.status='verified')
                            THEN 0 ELSE 1 END,p.next_check_at,p.competitor_id,p.ean LIMIT ?""",
                                        (row["id"], now_text, max_pairs)).fetchall()
                offer_interval = continuity_config()["offer_check_interval_hours"]
                started = time.monotonic()
                for pair in pairs:
                    self.heartbeat()
                    current = continuity_row(self.store, row["id"])
                    if (not current or current["status"] != "active"
                            or time.monotonic() - started >= deadline_seconds):
                        break
                    cid, ean = pair["competitor_id"], pair["ean"]
                    started_at = self.clock().isoformat()
                    with self.store._connect() as con, con:
                        claimed = con.execute("""UPDATE price_continuity_pairs SET state='running',started_at=?
                            WHERE id=? AND state='pending'""", (started_at, pair["id"])).rowcount
                    if not claimed:
                        continue
                    with self.execution(0, cid, ean, program="continuity", program_id=row["id"]):
                        before = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                        with self.store._connect() as con:
                            first_request = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]
                        result = batch.execute([ean], [cid], approved=True, sync_vytria=False, max_jobs=1)
                        after = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                        with self.store._connect() as con:
                            receipts = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                                WHERE competitor_id=? AND ean=? AND id>? ORDER BY id""",
                                                                      (cid, ean, first_request))]
                    result["inventory_before"] = before
                    result["inventory_after"] = after
                    result["http_receipts"] = receipts
                    pair_result = next((p for p in result["pairs"]
                                        if p["competitor_id"] == cid and p["ean"] == ean), {})
                    category = pair_result.get("category", "not_executed")
                    next_value = (after.get("next_check_at") or after.get("access_retry_at")
                                  or after.get("not_before") or after.get("valid_until"))
                    try:
                        next_check = _utc_datetime(next_value) if next_value else None
                    except (TypeError, ValueError):
                        next_check = None
                    if after.get("located") and after.get("last_ok_at"):
                        try:
                            verified_due = _utc_datetime(after["last_ok_at"]) + timedelta(hours=offer_interval)
                            next_check = min(next_check, verified_due) if next_check else verified_due
                        except (TypeError, ValueError):
                            LOG.warning("No se pudo interpretar last_ok_at para %s/%s", cid, ean)
                    reason = after.get("defer_reason") or pair_result.get("reason") or ""
                    if not reason and category == "reused":
                        reason = "Oferta verificada reutilizada; se reevalúa al vencer su vigencia."
                    if next_check and next_check > self.clock():
                        state = "pending"
                    elif any(word in reason.lower() for word in ("revisión", "revision", "review")):
                        state, next_check = "review", None
                    elif not next_check:
                        state = "restricted" if not pair_result.get("processed") else "review"
                        reason = reason or "Sin próxima fecha segura; revisión manual necesaria."
                    else:
                        state, next_check = "review", None
                        reason = reason or "La próxima comprobación no queda en el futuro; revisión manual necesaria."
                    receipt = {"result": result, "pair": pair_result}
                    finished_at = self.clock().isoformat()
                    robots = sum(_is_robots_receipt(r) for r in receipts)
                    commercial = len(receipts) - robots
                    unconfirmed = sum(r["result"] in {"", "reserved"} for r in receipts)
                    with self.store._connect() as con, con:
                        con.execute("""INSERT INTO price_continuity_checks
                            (continuity_id,competitor_id,ean,started_at,finished_at,state,category,requests,
                             http_commercial,http_robots,unconfirmed_http,next_check_at,defer_reason,receipt)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (row["id"], cid, ean, started_at, finished_at, state, category, len(receipts),
                                     commercial, robots, unconfirmed,
                                     next_check.isoformat() if next_check else None, reason,
                                     json.dumps(receipt, ensure_ascii=False)))
                        con.execute("""UPDATE price_continuity_pairs SET state=?,next_check_at=?,defer_reason=?,
                            checked_at=? WHERE id=?""",
                                    (state, next_check.isoformat() if next_check else None, reason,
                                     finished_at, pair["id"]))
        except Timeout:
            return

    def continuity_report(self) -> dict | None:
        row = continuity_row(self.store)
        if not row:
            return None
        row["manifest"] = json.loads(row["manifest"])
        row["limits"] = json.loads(row["limits"])
        row["sync_receipt"] = json.loads(row["sync_receipt"])
        with self.store._connect() as con:
            pairs = [dict(r) for r in con.execute("""SELECT * FROM price_continuity_pairs
                WHERE continuity_id=? ORDER BY competitor_id,ean""", (row["id"],))]
            checks = [dict(r) for r in con.execute("""SELECT * FROM price_continuity_checks
                WHERE continuity_id=? ORDER BY started_at,id""", (row["id"],))]
            audits = [dict(r) for r in con.execute("""SELECT action,previous,current,actor,note,at
                FROM access_audit WHERE scope='price_continuity' AND scope_id=? ORDER BY id""",
                                                    (str(row["id"]),))]
        for check in checks:
            check["next_check_at_madrid"] = (
                _utc_datetime(check["next_check_at"]).astimezone(ZoneInfo("Europe/Madrid")).isoformat()
                if check["next_check_at"] else None
            )
        for field in ("prepared_at", "started_at", "paused_at", "stopped_at", "heartbeat_at", "next_sync_at"):
            row[field + "_madrid"] = (
                _utc_datetime(row[field]).astimezone(ZoneInfo("Europe/Madrid")).isoformat()
                if row[field] else None
            )
        latest = {(check["competitor_id"], check["ean"]): check for check in checks}
        receipts = {}
        checked_pairs, reused_pairs = set(), set()
        checks_with_http = 0
        reused_events = 0
        for check in checks:
            receipt = json.loads(check["receipt"])
            result = receipt.get("result", {})
            requests = result.get("http_receipts", receipt.get("http_receipts", []))
            for request in requests:
                receipts[request["id"]] = request
            key = (check["competitor_id"], check["ean"])
            if any(request["result"].startswith(("HTTP ", "error:")) for request in requests):
                checked_pairs.add(key)
                checks_with_http += 1
            if check["category"] == "reused":
                reused_pairs.add(key)
                reused_events += 1
        row["pairs"] = [{
            **pair,
            "next_check_at_madrid": (
                _utc_datetime(pair["next_check_at"]).astimezone(ZoneInfo("Europe/Madrid")).isoformat()
                if pair["next_check_at"] else None
            ),
            "latest_check": latest.get((pair["competitor_id"], pair["ean"])),
        } for pair in pairs]
        row["checks"] = checks
        row["audits"] = audits
        confirmed = {request_id: request for request_id, request in receipts.items()
                     if request["result"].startswith(("HTTP ", "error:"))}
        row["summary"] = {
            "selected_pairs": len(row["manifest"]["pairs"]),
            "distinct_eans": len(row["manifest"]["eans"]),
            "pairs_with_real_http": len(checked_pairs),
            "checks_with_real_http": checks_with_http,
            "reused_offers": reused_events,
            "pairs_with_reused_offer": len(reused_pairs),
            "deferred_pairs": sum(pair["state"] == "pending" for pair in pairs),
            "restricted_pairs": sum(pair["state"] == "restricted" for pair in pairs),
            "review_pairs": sum(pair["state"] == "review" for pair in pairs),
            "http_reserved_unique": len(receipts),
            "http_confirmed_unique": len(confirmed),
            "http_unconfirmed_unique": sum(request["result"] in {"", "reserved"}
                                           for request in receipts.values()),
            "http_not_sent_unique": sum(request["result"].startswith("not_sent:")
                                        for request in receipts.values()),
            "http_commercial_unique": sum(not _is_robots_receipt(request)
                                          for request in confirmed.values()),
            "http_robots_unique": sum(_is_robots_receipt(request) for request in confirmed.values()),
        }
        from price_monitor import PriceMonitor
        inventory = PriceMonitor(self.store, clock=self.clock).inventory()
        selected = inventory[inventory["competitor_id"].isin(row["manifest"]["competitors"])
                             & inventory["ean"].isin(row["manifest"]["eans"])]
        row["summary"]["current_comparable_offers"] = int(selected["comparable"].fillna(False).sum())
        row["summary"]["expired_offers"] = int(selected["status"].eq("Precio caducado").sum())
        return row

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
            row["recoveries"] = [{**dict(r), "note": json.loads(r["note"])} for r in con.execute(
                """SELECT id,actor,note,at FROM access_audit WHERE scope='price_trial' AND scope_id='1'
                AND action='resume_initial_sync_failure' ORDER BY id""")]
        row["summary"] = {}
        receipts_by_group = {}
        queried_pairs_by_group = {}
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
                "selected_pairs": len(row["manifest"]["eans"]),
                "distinct_eans": len(row["manifest"]["eans"]),
                "processed_pairs": 0, "pairs_with_real_http": 0,
                "http": 0, "http_commercial": 0, "http_robots": 0,
                "reserved_http": 0, "unconfirmed_http": 0,
                "new_fiches": 0, "reused_fiches": 0,
                "comparable": 0, "out_of_stock": 0, "review": 0, "not_located": 0,
                "restricted_or_unavailable": 0, "deferred": 0})
            segments = saved["receipt"].get("segments", [])
            results = [p for segment in segments for p in segment["pairs"]]
            outcomes = [o for p in results for o in p["outcomes"]]
            totals["processed_pairs"] += int(any(p["processed"] for p in results))
            receipts = {r["id"]: r for s in segments for r in s.get("http_receipts", [])}
            receipts.update({r["id"]: r for r in saved["receipt"].get("interrupted_http_receipts", [])})
            receipts_by_group.setdefault(key, {}).update(receipts)
            if any(r["result"].startswith(("HTTP ", "error:")) for r in receipts.values()):
                queried_pairs_by_group.setdefault(key, set()).add((saved["competitor_id"], saved["ean"]))
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
            totals["not_located"] += int(any(o["state"] == "not_found" for o in outcomes) and not located)
            totals["restricted_or_unavailable"] += int(any(
                o["state"] in {"unavailable", "blocked"} for o in outcomes))
            totals["deferred"] += int(not results or results[-1]["category"] == "not_executed")
        for key, totals in row["summary"].items():
            receipts = receipts_by_group.get(key, {})
            confirmed = {request_id: request for request_id, request in receipts.items()
                         if request["result"].startswith(("HTTP ", "error:"))}
            totals["pairs_with_real_http"] = len(queried_pairs_by_group.get(key, set()))
            totals["http"] = len(confirmed)
            totals["http_commercial"] = sum(not _is_robots_receipt(request) for request in confirmed.values())
            totals["http_robots"] = sum(_is_robots_receipt(request) for request in confirmed.values())
            totals["reserved_http"] = len(receipts)
            totals["unconfirmed_http"] = sum(request["result"] in {"", "reserved"}
                                             for request in receipts.values())
        row["selection"] = {"selected_pairs_per_cycle": len(row["manifest"]["pairs"]),
                            "distinct_eans": len(row["manifest"]["eans"]),
                            "competitors": row["manifest"]["competitors"]}
        from price_monitor import PriceMonitor
        inventory = PriceMonitor(self.store, clock=self.clock).inventory()
        selected = inventory[inventory["competitor_id"].isin(row["manifest"]["competitors"])
                             & inventory["ean"].isin(row["manifest"]["eans"])]
        row["inventory_snapshot_at"] = self.clock().isoformat()
        row["offers_current_comparable"] = int(selected["comparable"].fillna(False).sum())
        row["offers_expired"] = int(selected["status"].eq("Precio caducado").sum())
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
                    service = PriceTrial(store)
                    service.tick()
                    service.continuity_tick()
            except Exception:
                LOG.exception("Price watch worker failed; no automatic reactivation")
            stopped.wait(30)

    thread = threading.Thread(target=worker, name="persistent-price-watch", daemon=True)
    thread.start()
    try:
        return child.wait()
    finally:
        stopped.set()
        thread.join(timeout=20)


def main(argv=None):
    # The script is __main__, but all guards import price_trial. The supervisor
    # must use that same module and ContextVar, not a second copy created here.
    from price_trial import serve as canonical_serve

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve requires the app command")
    return canonical_serve(args.serve)


if __name__ == "__main__":
    raise SystemExit(main())
