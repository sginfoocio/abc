"""Persistent, explicitly activated price-watch pilot and continuity workflows."""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit
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


class DiagnosticRetryError(ValueError):
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
        if owned:
            return "Par reservado al piloto de 72 h; no se ejecuta desde otro disparador"
        continuity = continuity_row(store)
        if continuity:
            selected = json.loads(continuity["manifest"])
            if cid in selected["competitors"] and (not ean or ean in selected["eans"]):
                return "Par reservado a la vigilancia de continuidad; no se ejecuta desde otro disparador"
        return ""
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


def _capture_url_guard(url):
    parsed = urlsplit(url)
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment
            or re.search(r"(?i)(csrf|password|passwd|cookie|secret|token|session|authorization|api[_-]?key)",
                         unquote(url))
            or any(key not in {"variant", "selectedSize"} or
                   not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value)
                   for key, value in parameters)):
        raise ConnectorError("URL con parámetros no admitidos para una captura segura")


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

    def continuity_expansion_preflight(self) -> dict:
        """Local-only proposal for the explicitly approved 100 EAN x 14 shops.

        The original pilot and its six-shop manifest file remain immutable. The
        existing worker already reads its persisted continuity selection; no
        general scheduler or second Vytria synchronizer is enabled here.
        """
        from competitor_requirements import COMPETITORS
        from price_access import AccessControl

        original = manifest()
        row = continuity_row(self.store)
        if not row or row["status"] != "active":
            raise ValueError("La ampliación requiere una continuidad activa")
        current = json.loads(row["manifest"])
        if current["eans"] != original["eans"]:
            raise ValueError("La ampliación solo admite los mismos 100 EAN originales")
        expected = {name: (urlsplit(url).hostname or "").removeprefix("www.")
                    for name, url in COMPETITORS}
        if len(expected) != 14:
            raise ValueError("La ampliación debe contener exactamente los 14 competidores documentados")
        with self.store._connect() as con:
            shops = {r["id"]: r["name"] for r in con.execute("SELECT * FROM competitors WHERE active=1")
                     if expected.get(r["name"]) == r["domain"]}
            watched = {r[0] for r in con.execute("SELECT ean FROM watch_products WHERE active=1")}
            pairs = {(r[0], r[1]) for r in con.execute(
                "SELECT competitor_id,ean FROM price_continuity_pairs WHERE continuity_id=?", (row["id"],))}
            beat = con.execute("SELECT value FROM comparator_settings WHERE key=?", (HEARTBEAT_KEY,)).fetchone()
            original_trial = con.execute("SELECT status FROM price_trial WHERE id=1").fetchone()
        if not original_trial or original_trial["status"] not in {"expired", "stopped"}:
            raise ValueError("La ampliación no puede solaparse con el piloto original")
        if len(shops) != 14 or set(shops.values()) != set(expected):
            raise ValueError("Faltan competidores activos o su identidad no coincide con los 14 documentados")
        if not set(original["eans"]) <= watched:
            raise ValueError("Faltan EAN originales en la vigilancia activa")
        if not beat or not timedelta(0) <= self.clock() - datetime.fromisoformat(beat[0]) < timedelta(minutes=2):
            raise ValueError("No hay heartbeat reciente del trabajador de continuidad")
        if current["competitors"] not in (original["competitors"], shops):
            raise ValueError("El alcance actual no coincide con la selección original ni con la ampliación")
        old_pairs = {(cid, ean) for cid in current["competitors"] for ean in original["eans"]}
        if pairs != old_pairs or {(p["competitor_id"], p["ean"]) for p in current["pairs"]} != old_pairs:
            raise ValueError("Los pares persistidos no coinciden con el manifiesto actual")
        access = AccessControl(self.store, clock=self.clock)
        for cid in shops:
            if any(access.method_status(cid, method) != "authorized" for method in ("ficha", "sitemap")):
                raise ValueError(f"Falta autorización de fichas o sitemaps para {shops[cid]}")
        expanded = {**original, "competitors": shops,
                    "pairs": [{"competitor_id": cid, "ean": ean} for cid in shops for ean in original["eans"]]}
        limits = json.loads(row["limits"])
        if not set(current["competitors"]) <= set(limits):
            raise ValueError("Faltan presupuestos congelados del alcance actual")
        for cid in shops.keys() - limits.keys():
            budget = access.budget("competitor", cid)
            limits[cid] = {**budget, "daily_limit": min(budget["daily_limit"], 200),
                           "per_minute_limit": min(budget["per_minute_limit"], 10),
                           "effective_pause": max(budget["effective_pause"], 3.0)}
        return {"row": row, "manifest": expanded, "limits": limits,
                "additional_pairs": len(expanded["pairs"]) - len(old_pairs)}

    def expand_continuity(self, *, actor: str, approved: bool, build_commit: str, note: str) -> dict:
        """Atomically add only the 800 missing pairs, preserving all existing work.

        This is a separate explicit scope decision, not a restart of the pilot.
        The audit retains the complete previous manifest and budgets. No HTTP,
        synchronization, review acceptance, permission or scheduler change occurs.
        """
        if not approved or not actor.strip() or not note.strip():
            raise ValueError("Confirmación explícita, responsable y motivo obligatorios")
        if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
            raise ValueError("Commit desplegado válido requerido")
        with self.continuity_lock(), ExitStack() as stack:
            prepared = self.continuity_expansion_preflight()
            row = prepared["row"]
            if not prepared["additional_pairs"]:
                return self.continuity_report()
            for cid in sorted(prepared["manifest"]["competitors"]):
                stack.enter_context(FileLock(f"{self.store.database_path}.competitor-{cid}.lock", timeout=0))
            stack.enter_context(FileLock(f"{self.store.database_path}.vytria-sync.lock", timeout=0))
            old_manifest = json.loads(row["manifest"])
            additions = [p for p in prepared["manifest"]["pairs"]
                         if p["competitor_id"] not in old_manifest["competitors"]]
            from price_access import AccessControl
            with self.store._connect() as con, con:
                changed = con.execute("""UPDATE price_continuity SET manifest=?,limits=?
                    WHERE id=? AND status='active' AND manifest=? AND limits=?""",
                                      (json.dumps(prepared["manifest"], ensure_ascii=False),
                                       json.dumps(prepared["limits"]), row["id"], row["manifest"],
                                       row["limits"])).rowcount
                if changed != 1:
                    raise ValueError("La continuidad cambió antes de aplicar la ampliación")
                con.executemany("""INSERT INTO price_continuity_pairs
                    (continuity_id,competitor_id,ean) VALUES(?,?,?)""",
                                [(row["id"], p["competitor_id"], p["ean"]) for p in additions])
                AccessControl._audit(
                    con, "price_continuity", str(row["id"]), "expand_scope", "600", "1400", actor.strip(),
                    json.dumps({"reason": note.strip(), "build_commit": build_commit,
                                "previous_manifest": old_manifest, "previous_limits": json.loads(row["limits"]),
                                "added_competitors": sorted(set(prepared["manifest"]["competitors"])
                                                            - set(old_manifest["competitors"])),
                                "added_pairs": len(additions)}, ensure_ascii=False), self.clock().isoformat())
        return self.continuity_report()

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

    def _close_continuity_failure(self, continuity_id, pair, started_at, *, phase, error_type=""):
        finished_at = self.clock().isoformat()
        with self.store._connect() as con, con:
            requests = [dict(r) for r in con.execute("""SELECT id,competitor_id,ean,scope,scope_id,reserved_at,result,url
                FROM access_requests ORDER BY id""")]
            relevant = []
            uncertain = False
            for request in requests:
                try:
                    if _utc_datetime(request["reserved_at"]) >= _utc_datetime(started_at):
                        uncertain = True
                        relevant.append(request)
                except (TypeError, ValueError):
                    uncertain = True
                    relevant.append(request)
            safe = phase in {"paused_before_execution", "preview_before_execution"} and not uncertain
            controlled = safe and phase == "paused_before_execution"
            category = ("controlled_stop_no_http" if controlled else
                        "technical_no_http" if safe else "interrupted")
            state = "pending" if controlled else "review"
            reason = ("Pausa controlada antes de ejecutar; sin reservas posteriores"
                      if controlled else "Error técnico antes de ejecutar; sin reservas posteriores"
                      if safe else "Trabajador interrumpido; no se repite una reserva incierta")
            pair_receipts = [r for r in relevant if r["competitor_id"] == pair["competitor_id"]
                             and r["ean"] == pair["ean"]]
            redacted_receipts = [{"id": r["id"],
                                  "result": (r["result"] if re.fullmatch(
                                      r"HTTP [0-9]{3}|error: [A-Za-z0-9_]+|reserved", r["result"] or "")
                                             else "reserved"),
                                  "url": "/robots.txt" if _is_robots_receipt(r) else ""}
                                 for r in pair_receipts]
            receipt = {"closure_reason": reason, "phase": phase, "error_type": error_type,
                       "reservation_check": "none_since_start" if not uncertain else "uncertain",
                       "reservation_ids_since_start": [r["id"] for r in relevant],
                       "http_receipts": redacted_receipts}
            con.execute("""INSERT INTO price_continuity_checks
                (continuity_id,competitor_id,ean,started_at,finished_at,state,category,requests,http_commercial,
                 http_robots,unconfirmed_http,defer_reason,receipt)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (continuity_id, pair["competitor_id"], pair["ean"], started_at, finished_at,
                         state, category, len(pair_receipts),
                         sum(not _is_robots_receipt(r) for r in pair_receipts),
                         sum(_is_robots_receipt(r) for r in pair_receipts),
                         sum(r["result"] == "reserved" for r in redacted_receipts),
                         reason, json.dumps(receipt, ensure_ascii=False)))
            con.execute("""UPDATE price_continuity_pairs SET state=?,checked_at=?,defer_reason=?
                WHERE id=? AND continuity_id=? AND state='running' AND started_at=?""",
                        (state, finished_at, reason, pair["id"], continuity_id, started_at))

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

    def continuity_tick(self, *, max_pairs: int = 20, deadline_seconds: int = 240,
                        exact_pair: tuple[str, str] | None = None) -> int | None:
        try:
            with self.continuity_lock():
                if exact_pair is None:
                    self.heartbeat()
                row = continuity_row(self.store)
                if not row or row["status"] != "active":
                    if exact_pair is not None:
                        raise ValueError("La continuidad no está activa")
                    return
                if exact_pair is None:
                    self._close_continuity_interrupted(row["id"])
                now = self.clock()
                if now < datetime.fromisoformat(row["started_at"]):
                    return
                if exact_pair is None and row["next_sync_at"] and datetime.fromisoformat(row["next_sync_at"]) <= now:
                    self._run_continuity_sync(row)
                from price_batch import WatchBatch
                batch = self._batch or WatchBatch(self.store)
                now_text = self.clock().isoformat()
                with self.store._connect() as con:
                    pairs = con.execute("""SELECT p.* FROM price_continuity_pairs p
                        WHERE p.continuity_id=? AND p.state='pending'
                          AND (p.next_check_at IS NULL OR p.next_check_at<=?)
                          AND (? IS NULL OR (p.competitor_id=? AND p.ean=?))
                        ORDER BY CASE WHEN EXISTS(SELECT 1 FROM competitor_product_urls u
                            WHERE u.competitor_id=p.competitor_id AND u.ean=p.ean AND u.status='verified')
                            THEN 0 ELSE 1 END,p.next_check_at,p.competitor_id,p.ean LIMIT ?""",
                                        (row["id"], now_text, exact_pair[0] if exact_pair else None,
                                         *(exact_pair or ("", "")), max_pairs)).fetchall()
                if exact_pair is not None and not pairs:
                    raise ValueError("El par no está pendiente y vencido; no se fuerza su procesamiento")
                if exact_pair is not None:
                    with self.store._connect() as con:
                        verified = con.execute("""SELECT 1 FROM competitor_product_urls
                            WHERE competitor_id=? AND ean=? AND status='verified' LIMIT 1""", exact_pair).fetchone()
                        review = con.execute("""SELECT 1 FROM competitor_product_urls WHERE competitor_id=? AND ean=?
                            AND status IN ('probable','pending_verification')
                            UNION ALL SELECT 1 FROM pilot_url_candidates WHERE competitor_id=? AND ean=?
                            AND state IN ('candidate_found','pending_validation')
                            UNION ALL SELECT 1 FROM locate_jobs WHERE competitor_id=? AND ean=?
                            AND state!='done'
                            UNION ALL SELECT 1 FROM monitor_jobs WHERE competitor_id=? AND ean=?
                            AND state IN ('review','failed','running') LIMIT 1""", exact_pair * 4).fetchone()
                    if not verified:
                        raise ValueError("La captura requiere una ficha ya verificada; no acepta revisiones")
                    if review:
                        raise ValueError("Hay trabajos o identidades pendientes de revisión; no se fuerza la captura")
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
                    current = continuity_row(self.store, row["id"])
                    if not current or current["status"] != "active":
                        self._close_continuity_failure(row["id"], pair, started_at, phase="paused_before_execution")
                        break
                    phase = "preview_before_execution"
                    try:
                        with self.execution(0, cid, ean, program="continuity", program_id=row["id"]):
                            before = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                            with self.store._connect() as con:
                                first_request = con.execute("SELECT COALESCE(MAX(id),0) FROM access_requests").fetchone()[0]
                            phase = "execution_started"
                            result = batch.execute([ean], [cid], approved=True, sync_vytria=False, max_jobs=1)
                            after = json.loads(batch.preview([ean], [cid]).to_json(orient="records"))[0]
                            with self.store._connect() as con:
                                receipts = [dict(r) for r in con.execute("""SELECT * FROM access_requests
                                    WHERE competitor_id=? AND ean=? AND id>? ORDER BY id""",
                                                                          (cid, ean, first_request))]
                    except Exception as exc:
                        LOG.error("Error en comprobación de continuidad %s/%s (%s, %s)",
                                  cid, ean, phase, type(exc).__name__)
                        self._close_continuity_failure(row["id"], pair, started_at, phase=phase,
                                                       error_type=type(exc).__name__)
                        continue
                    result["inventory_before"] = before
                    result["inventory_after"] = after
                    result["http_receipts"] = receipts
                    pair_result = next((p for p in result["pairs"]
                                        if p["competitor_id"] == cid and p["ean"] == ean), {})
                    category = pair_result.get("category", "not_executed")
                    next_value = (after.get("next_check_at") or after.get("access_retry_at")
                                  or after.get("not_before") or after.get("valid_until"))
                    invalid_deadline = False
                    try:
                        next_check = _utc_datetime(next_value) if next_value else None
                    except (TypeError, ValueError):
                        next_check = None
                        invalid_deadline = True
                    for restriction in (after.get("not_before"), after.get("access_retry_at")):
                        if restriction:
                            try:
                                limit = _utc_datetime(restriction)
                            except (TypeError, ValueError):
                                LOG.warning("Fecha de aplazamiento inválida para %s/%s", cid, ean)
                                invalid_deadline = True
                                continue
                            if next_check is None or limit > next_check:
                                next_check = limit
                    if after.get("located") and after.get("last_ok_at"):
                        try:
                            verified_due = _utc_datetime(after["last_ok_at"]) + timedelta(hours=offer_interval)
                            if not (after.get("not_before") or after.get("access_retry_at")):
                                next_check = min(next_check, verified_due) if next_check else verified_due
                        except (TypeError, ValueError):
                            LOG.warning("No se pudo interpretar last_ok_at para %s/%s", cid, ean)
                    reason = after.get("defer_reason") or pair_result.get("reason") or ""
                    if not reason and category == "reused":
                        reason = "Oferta verificada reutilizada; se reevalúa al vencer su vigencia."
                    if invalid_deadline:
                        state, next_check = "review", None
                        reason = "Fecha de aplazamiento inválida; requiere revisión."
                    elif next_check and next_check > self.clock():
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
                        check_id = con.execute("""INSERT INTO price_continuity_checks
                            (continuity_id,competitor_id,ean,started_at,finished_at,state,category,requests,
                             http_commercial,http_robots,unconfirmed_http,next_check_at,defer_reason,receipt)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (row["id"], cid, ean, started_at, finished_at, state, category, len(receipts),
                                     commercial, robots, unconfirmed,
                                     next_check.isoformat() if next_check else None, reason,
                                     json.dumps(receipt, ensure_ascii=False))).lastrowid
                        con.execute("""UPDATE price_continuity_pairs SET state=?,next_check_at=?,defer_reason=?,
                            checked_at=? WHERE id=?""",
                                    (state, next_check.isoformat() if next_check else None, reason,
                                     finished_at, pair["id"]))
                    if exact_pair is not None:
                        return check_id
        except Timeout:
            if exact_pair is not None:
                raise
            return

    def capture_continuity(self, competitor_id: str, ean: str, *, actor: str, note: str,
                           build_commit: str, approved: bool) -> dict:
        """Capture a single ordinary, due continuity check; never starts a second HTTP consumer."""
        if not approved or not actor.strip() or not note.strip():
            raise ValueError("Responsable, motivo y aprobación explícita obligatorios")
        if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
            raise ValueError("Commit válido de 40 caracteres requerido")
        ean = normalize_gtin(ean)
        from price_monitor import capture_context

        evidence = []
        failure = []
        sensitive = re.compile(rb"(?i)(csrf|password|passwd|cookie|secret|token|session|authorization|api[_-]?key)")

        def capture(observed_cid, observed_ean, fetched, page, identity):
            if (observed_cid, observed_ean) != (competitor_id, ean):
                return
            body = fetched.content
            metadata = json.dumps(fetched.trace, ensure_ascii=False).encode("utf-8")
            if len(body) > 1024 * 1024:
                failure.append("La respuesta supera el límite de captura de 1 MiB")
            elif sensitive.search(body) or sensitive.search(metadata):
                failure.append("La respuesta o URL puede contener datos sensibles; no se conserva")
            elif not re.search(rb"(?i)<(?:!doctype\s+html|html|head|body)\b", body[:4096]):
                failure.append("La respuesta no acredita contenido HTML")
            else:
                from price_web_extract import _Collector
                collector = _Collector()
                collector.feed(fetched.text)
                evidence.append({"captured_at": self.clock().isoformat(),
                                 "requested_url": fetched.requested_url, "final_url": fetched.url,
                                 "http_status": fetched.status, "redirects": fetched.redirects,
                                 "canonical_urls": list(dict.fromkeys(urljoin(fetched.url, href)
                                                                       for href in collector.canonicals)),
                                 "sha256": hashlib.sha256(body).hexdigest(),
                                 "html_base64": base64.b64encode(body).decode("ascii"),
                                 "extraction": asdict(page) if page else None, "identity": identity})

        with capture_context(capture, _capture_url_guard):
            check_id = self.continuity_tick(max_pairs=1, exact_pair=(competitor_id, ean))
        if failure:
            raise ValueError(failure[0])
        if len(evidence) != 1 or check_id is None:
            raise ValueError("No hubo una respuesta nueva capturable; consulte el recibo de continuidad")
        item = {"continuity_check_id": check_id, "competitor_id": competitor_id, "ean": ean,
                "build_commit": build_commit, **evidence[0]}
        with self.store._connect() as con, con:
            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                VALUES('competitor',?,'capture_continuity_html','',?,?,?,?)""",
                        (competitor_id, json.dumps(item, ensure_ascii=False), actor.strip(), note.strip(),
                         item["captured_at"]))
        return item

    def diagnostic_candidates(self, competitor_id: str, ean: str) -> list[str]:
        """Only URLs already associated with this exact pair and still under review."""
        with self.store._connect() as con:
            urls = {r[0] for r in con.execute("""SELECT url FROM competitor_product_urls
                WHERE competitor_id=? AND ean=? AND status IN ('probable','pending_verification')
                UNION SELECT url FROM pilot_url_candidates WHERE competitor_id=? AND ean=?
                AND state IN ('candidate_found','pending_validation')
                UNION SELECT url FROM locate_jobs WHERE competitor_id=? AND ean=? AND state='review' AND url!=''""",
                                                   (competitor_id, ean) * 3)}
            checks = con.execute("""SELECT receipt FROM price_continuity_checks WHERE competitor_id=? AND ean=?
                AND state='review' ORDER BY id DESC""", (competitor_id, ean)).fetchall()
            excluded = {r[0] for r in con.execute("""SELECT url FROM competitor_product_urls
                WHERE competitor_id=? AND ean=? AND status IN ('verified','rejected','discarded','replaced')
                UNION SELECT url FROM pilot_url_candidates WHERE competitor_id=? AND ean=?
                AND state IN ('rejected','discarded')""", (competitor_id, ean) * 2)}
        for check in checks:
            receipt = json.loads(check["receipt"])
            for pair in receipt.get("result", {}).get("pairs", []):
                if (pair.get("competitor_id"), pair.get("ean")) != (competitor_id, ean):
                    continue
                for outcome in pair.get("outcomes", []):
                    if outcome.get("state") == "review" and outcome.get("url"):
                        urls.add(outcome["url"])
        return sorted(urls - excluded)

    def request_diagnostic(self, competitor_id, ean, url, *, actor, note, build_commit, approved):
        from price_diagnostic import _SECRET
        actor, note = actor.strip(), note.strip()
        try:
            if not approved or not actor or not note:
                raise ValueError("Responsable, motivo y aprobación explícita obligatorios")
            if _SECRET.search(actor) or _SECRET.search(note):
                raise ValueError("No incluya datos sensibles en responsable o motivo")
            if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
                raise ValueError("Commit válido de 40 caracteres requerido")
            ean = normalize_gtin(ean)
            _capture_url_guard(url)
            row = continuity_row(self.store)
            if not row or row["status"] not in {"active", "paused"}:
                raise ValueError("No hay continuidad activa o pausada")
            frozen = json.loads(row["manifest"])
            if competitor_id not in frozen["competitors"] or ean not in frozen["eans"]:
                raise ValueError("Par fuera del manifiesto")
            if url not in self.diagnostic_candidates(competitor_id, ean):
                raise DiagnosticRetryError("La URL no es una candidata documentada pendiente de revisión")
        except (ValueError, RuntimeError) as exc:
            with self.store._connect() as con, con:
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('competitor',?,'request_diagnostic','',?,?,?,?)""",
                            (competitor_id, json.dumps({"status": "rejected", "error_type": type(exc).__name__,
                                                        "url_sha256": hashlib.sha256(url.encode()).hexdigest()}),
                             actor if actor and not _SECRET.search(actor) else "sin identificar",
                             "", self.clock().isoformat()))
            raise
        with self.store._connect() as con, con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("""SELECT id,status FROM price_diagnostic_requests
                WHERE continuity_id=? AND competitor_id=? AND ean=? AND url=?
                ORDER BY id DESC LIMIT 1""", (row["id"], competitor_id, ean, url)).fetchone()
            if not existing or existing["status"] == "expired":
                con.execute("""INSERT INTO price_diagnostic_requests
                    (continuity_id,competitor_id,ean,url,actor,note,build_commit,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                            (row["id"], competitor_id, ean, url, actor, note, build_commit, self.clock().isoformat()))
            saved = dict(con.execute("""SELECT id,status FROM price_diagnostic_requests
                WHERE continuity_id=? AND competitor_id=? AND ean=? AND url=?
                ORDER BY id DESC LIMIT 1""",
                                     (row["id"], competitor_id, ean, url)).fetchone())
            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                VALUES('competitor',?,'request_diagnostic','',?,?,?,?)""",
                        (competitor_id, json.dumps(saved), actor, note, self.clock().isoformat()))
        return saved

    def _diagnostic_retry_guard(self, con, previous):
        if (previous["status"] != "rejected" or not previous["started_at"] or not previous["finished_at"]
                or previous["artifact"] is not None):
            raise DiagnosticRetryError("El intento anterior no es un rechazo previo a HTTP acreditado")
        try:
            result = json.loads(previous["result"])
            start = _utc_datetime(previous["started_at"])
            end = _utc_datetime(previous["finished_at"])
        except (ValueError, TypeError):
            raise DiagnosticRetryError("Resultado o fechas del intento anterior inválidos") from None
        if (not isinstance(result, dict) or end < start or result.get("status") != "rejected"
                or result.get("stage") != "lock_and_scope" or result.get("error_type") != "EvidenceError"
                or result.get("reason") != "El almacenamiento no acredita permisos privados"
                or previous["reason"] != result.get("reason")
                or result.get("queue_id") != previous["id"] or result.get("requests") != []
                or result.get("ean") != previous["ean"]
                or result.get("url_sha256") != hashlib.sha256(previous["url"].encode()).hexdigest()
                or result.get("build_commit") != previous["build_commit"]):
            raise DiagnosticRetryError("El rechazo no acredita inequívocamente un fallo previo a HTTP")
        audits = con.execute("""SELECT id,current FROM access_audit WHERE action='capture_diagnostic_html'
            AND json_extract(current,'$.queue_id')=?""", (previous["id"],)).fetchall()
        if len(audits) != 1:
            raise DiagnosticRetryError("Falta la auditoría inequívoca del intento anterior")
        try:
            audited = json.loads(audits[0]["current"])
        except (ValueError, TypeError):
            raise DiagnosticRetryError("Auditoría del intento anterior inválida") from None
        if audited != result:
            raise DiagnosticRetryError("El resultado no coincide con la auditoría del intento anterior")
        receipt = con.execute("""SELECT id FROM access_requests
            WHERE purpose=? OR ((competitor_id=? OR (scope='competitor' AND scope_id=?)
              OR (competitor_id='' AND (scope!='competitor' OR scope_id='')))
              AND (ean='' OR ean=?) AND (julianday(reserved_at) IS NULL
                   OR julianday(reserved_at) BETWEEN julianday(?) AND julianday(?))) LIMIT 1""",
                              (f"diagnostic:{audits[0]['id']}", previous["competitor_id"],
                               previous["competitor_id"], previous["ean"],
                               previous["started_at"], previous["finished_at"])).fetchone()
        if receipt:
            raise DiagnosticRetryError("Existe una petición o reserva aplicable al intento anterior")
        later_receipt = con.execute("""SELECT id FROM access_requests
            WHERE (competitor_id=? OR (scope='competitor' AND scope_id=?)
              OR (competitor_id='' AND (scope!='competitor' OR scope_id='')))
              AND (ean='' OR ean=?)
              AND (julianday(reserved_at) IS NULL
                   OR julianday(reserved_at)>=julianday(?)) LIMIT 1""",
                                    (previous["competitor_id"], previous["competitor_id"],
                                     previous["ean"], previous["finished_at"])).fetchone()
        if later_receipt:
            raise DiagnosticRetryError("Existe una petición o reserva posterior al rechazo previo")

    def _validate_diagnostic_pair(self, continuity_id, competitor_id, ean, url):
        if url not in self.diagnostic_candidates(competitor_id, ean):
            raise ValueError("La URL no es una candidata documentada pendiente de revisión")
        with self.store._connect() as con:
            pair = con.execute("""SELECT state,next_check_at FROM price_continuity_pairs
                WHERE continuity_id=? AND competitor_id=? AND ean=?""",
                               (continuity_id, competitor_id, ean)).fetchone()
            jobs = [con.execute(f"SELECT state,not_before FROM {table} WHERE competitor_id=? AND ean=?",
                                (competitor_id, ean)).fetchone()
                    for table in ("locate_jobs", "monitor_jobs")]
        if not pair or pair["state"] not in ("review", "pending"):
            raise DiagnosticRetryError("El par no está disponible para diagnóstico")
        for job in jobs:
            if job and (job["state"] in ("running", "blocked", "failed")
                        or (job["not_before"] and _utc_datetime(job["not_before"]) > self.clock())):
                raise DiagnosticRetryError("Hay un trabajo bloqueado, en ejecución o aplazado")
        if pair["next_check_at"] and _utc_datetime(pair["next_check_at"]) > self.clock():
            raise DiagnosticRetryError("El par está aplazado")

    def retry_diagnostic(self, request_id, *, actor, note, build_commit, approved):
        from price_batch import WatchBatch
        from price_diagnostic import EvidenceError, _SECRET, has_capacity, limits, require_private_database

        actor, note = actor.strip(), note.strip()
        previous = None
        try:
            if not approved or not actor or not note or _SECRET.search(actor) or _SECRET.search(note):
                raise ValueError("Responsable, motivo y aprobación explícita obligatorios, sin datos sensibles")
            if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
                raise ValueError("Commit válido de 40 caracteres requerido")
            with self.continuity_lock():
                with self.store._connect() as con:
                    row = con.execute("SELECT * FROM price_diagnostic_requests WHERE id=?", (request_id,)).fetchone()
                    if not row:
                        raise DiagnosticRetryError("Intento anterior inexistente")
                    previous = dict(row)
                    child = con.execute("SELECT id,status FROM price_diagnostic_requests WHERE retry_of=?",
                                        (request_id,)).fetchone()
                    if child:
                        saved = dict(child)
                        with con:
                            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                                VALUES('competitor',?,'retry_diagnostic',?,?,?,?,?)""",
                                        (previous["competitor_id"], str(request_id),
                                         json.dumps({**saved, "duplicate": True, "build_commit": build_commit}),
                                         actor, note,
                                         self.clock().isoformat()))
                        return saved
                batch = self._batch or WatchBatch(self.store)
                with batch.locator.search._lock(previous["competitor_id"]):
                    continuity = continuity_row(self.store)
                    if not continuity or continuity["status"] != "active" or continuity["id"] != previous["continuity_id"]:
                        raise DiagnosticRetryError("La continuidad original no está activa")
                    frozen = json.loads(continuity["manifest"])
                    if previous["competitor_id"] not in frozen["competitors"] or previous["ean"] not in frozen["eans"]:
                        raise DiagnosticRetryError("Par fuera del manifiesto")
                    self._validate_diagnostic_pair(continuity["id"], previous["competitor_id"],
                                                   previous["ean"], previous["url"])
                    require_private_database(self.store.database_path)
                    config = batch.locator.search.config(previous["competitor_id"], initialize=False)
                    gate = batch.locator.search._gate(config)
                    if gate:
                        raise DiagnosticRetryError(gate)
                    decision = batch.locator.access.check(previous["competitor_id"], "ficha")
                    if not decision.allowed:
                        raise DiagnosticRetryError(f"Acceso denegado: {decision.code}")
                    body_limit, quota, _ = limits()
                    with self.store._connect() as con, con:
                        con.execute("BEGIN IMMEDIATE")
                        current = con.execute("SELECT * FROM price_diagnostic_requests WHERE id=?", (request_id,)).fetchone()
                        self._diagnostic_retry_guard(con, current)
                        used = con.execute("SELECT COALESCE(SUM(length(artifact)),0) FROM price_diagnostic_requests").fetchone()[0]
                        if not has_capacity(self.store.database_path, used, body_limit, quota):
                            raise DiagnosticRetryError("Capacidad insuficiente para conservar la respuesta")
                        child = con.execute("SELECT id,status FROM price_diagnostic_requests WHERE retry_of=?",
                                            (request_id,)).fetchone()
                        if child:
                            return dict(child)
                        active = con.execute("""SELECT id FROM price_diagnostic_requests
                            WHERE continuity_id=? AND competitor_id=? AND ean=? AND url=?
                            AND status NOT IN ('expired','rejected')""",
                                             (previous["continuity_id"], previous["competitor_id"],
                                              previous["ean"], previous["url"])).fetchone()
                        if active:
                            raise DiagnosticRetryError("Ya existe otra solicitud activa para esta candidata")
                        created_at = self.clock().isoformat()
                        saved_id = con.execute("""INSERT INTO price_diagnostic_requests
                            (continuity_id,competitor_id,ean,url,actor,note,build_commit,created_at,retry_of)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                                               (previous["continuity_id"], previous["competitor_id"], previous["ean"],
                                                previous["url"], actor, note, build_commit, created_at,
                                                request_id)).lastrowid
                        saved = {"id": saved_id, "status": "pending", "retry_of": request_id,
                                 "actor": actor, "note": note, "build_commit": build_commit,
                                 "created_at": created_at}
                        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                            VALUES('competitor',?,'retry_diagnostic',?,?,?,?,?)""",
                                    (previous["competitor_id"], str(request_id),
                                     json.dumps({k: saved[k] for k in
                                                 ("id", "status", "retry_of", "build_commit", "created_at")}),
                                     actor, note,
                                     self.clock().isoformat()))
                        return saved
        except (ValueError, RuntimeError, Timeout) as exc:
            with self.store._connect() as con, con:
                reason = (str(exc) if isinstance(exc, (DiagnosticRetryError, EvidenceError))
                          else "Bloqueo ocupado" if isinstance(exc, Timeout) else type(exc).__name__)
                con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                    VALUES('competitor',?,'retry_diagnostic',?,?,?,?,?)""",
                            (previous["competitor_id"] if previous else "", str(request_id),
                             json.dumps({"status": "rejected", "error_type": type(exc).__name__, "reason": reason,
                                         "build_commit": build_commit if isinstance(build_commit, str)
                                         and len(build_commit) == 40
                                         and all(c in "0123456789abcdef" for c in build_commit) else ""}),
                             actor if actor and not _SECRET.search(actor) else "sin identificar",
                             note if not _SECRET.search(note) else "", self.clock().isoformat()))
            raise

    def diagnostic_results(self, competitor_id, ean):
        with self.store._connect() as con:
            rows = con.execute("""SELECT id,retry_of,url,actor,note,build_commit,status,reason,created_at,finished_at,
                result,expires_at,
                (artifact IS NOT NULL AND expires_at>?) AS available FROM price_diagnostic_requests
                WHERE competitor_id=? AND ean=? ORDER BY id DESC""",
                               (self.clock().isoformat(), competitor_id, ean)).fetchall()
        return [dict(row) for row in rows]

    def diagnostic_artifact(self, request_id):
        from price_diagnostic import expire
        with self.store._connect() as con, con:
            expire(con, self.clock())
            row = con.execute("SELECT artifact FROM price_diagnostic_requests WHERE id=?", (request_id,)).fetchone()
        if not row or row["artifact"] is None:
            raise ValueError("La evidencia no existe o ha caducado")
        return bytes(row["artifact"])

    def diagnostic_tick(self):
        from price_diagnostic import EvidenceError, expire, expiry, has_capacity, limits
        try:
            lock = self.continuity_lock()
            with lock:
                with self.store._connect() as con, con:
                    expire(con, self.clock())
                    interrupted = con.execute("""SELECT id,competitor_id FROM price_diagnostic_requests
                        WHERE status='running'""").fetchall()
                    con.execute("""UPDATE price_diagnostic_requests SET status='rejected',
                        reason='Interrupción con consumo incierto; no se repite automáticamente',finished_at=?
                        WHERE status='running'""", (self.clock().isoformat(),))
                    for item in interrupted:
                        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                            VALUES('competitor',?,'interrupt_diagnostic_request',?,'rejected','worker','',?)""",
                                    (item["competitor_id"], str(item["id"]), self.clock().isoformat()))
                continuity = continuity_row(self.store)
                if not continuity or continuity["status"] != "active":
                    return
                with self.store._connect() as con, con:
                    row = con.execute("""SELECT * FROM price_diagnostic_requests WHERE continuity_id=?
                        AND (status='pending' OR (status='deferred' AND reason='capacity'))
                        ORDER BY id LIMIT 1""", (continuity["id"],)).fetchone()
                    if not row:
                        return
                    request = dict(row)
                    body_limit, quota, _ = limits()
                    used = con.execute("""SELECT COALESCE(SUM(length(artifact)),0)
                        FROM price_diagnostic_requests""").fetchone()[0]
                    if not has_capacity(self.store.database_path, used, body_limit, quota):
                        if request["status"] != "deferred" or request["reason"] != "capacity":
                            con.execute("""UPDATE price_diagnostic_requests SET status='deferred',reason='capacity'
                                WHERE id=?""", (request["id"],))
                            con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                                VALUES('competitor',?,'defer_diagnostic_capacity',?,'deferred','worker','',?)""",
                                        (request["competitor_id"], str(request["id"]), self.clock().isoformat()))
                        return
                    con.execute("""UPDATE price_diagnostic_requests SET status='running',started_at=?
                        WHERE id=?""", (self.clock().isoformat(), request["id"]))
                try:
                    result = self.capture_diagnostic(request["competitor_id"], request["ean"], request["url"],
                                                     actor=request["actor"], note=request["note"],
                                                     build_commit=request["build_commit"], approved=True,
                                                     _held_lock=lock, _queue_id=request["id"])
                    artifact = base64.b64decode(result.pop("html_base64"), validate=True)
                    _, _, days = limits()
                    status, reason = "captured", ""
                except Timeout:
                    result, artifact, status, reason = {}, None, "pending", "Bloqueo ocupado"
                except (ValueError, RuntimeError) as exc:
                    with self.store._connect() as con:
                        audit = con.execute("""SELECT current FROM access_audit WHERE action='capture_diagnostic_html'
                            AND json_extract(current,'$.queue_id')=? ORDER BY id DESC LIMIT 1""",
                                            (request["id"],)).fetchone()
                    result = json.loads(audit["current"]) if audit else {"error_type": type(exc).__name__}
                    artifact = None
                    if str(exc) == "Capacidad insuficiente para conservar la respuesta":
                        status, reason = "deferred", "capacity"
                    else:
                        status = "rejected"
                        reason = result.get("reason") or (str(exc) if isinstance(exc, EvidenceError)
                                                          else type(exc).__name__)
                with self.store._connect() as con, con:
                    con.execute("""UPDATE price_diagnostic_requests SET status=?,reason=?,finished_at=?,result=?,
                        artifact=?,expires_at=? WHERE id=?""",
                                (status, reason, self.clock().isoformat(), json.dumps(result, ensure_ascii=False),
                                 artifact, expiry(self.clock(), days) if artifact else None, request["id"]))
        except Timeout:
            return

    def capture_diagnostic(self, competitor_id: str, ean: str, url: str, *, actor: str, note: str,
                           build_commit: str, approved: bool, _held_lock: FileLock | None = None,
                           _queue_id: int | None = None) -> dict:
        """Collect candidate HTML without changing any pair, identity, offer or review."""
        from price_batch import WatchBatch
        from price_pilot import PricePilot, pilot_scope
        from price_web import validate_competitor_url
        from price_diagnostic import EvidenceError, expire, has_capacity, limits, require_private_database, sanitise

        actor, note = actor.strip(), note.strip()
        started = self.clock().isoformat()
        with self.store._connect() as con, con:
            audit_id = con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                VALUES('competitor',?,'capture_diagnostic_html','',?,?,?,?)""",
                                   (competitor_id, json.dumps({"status": "started", "queue_id": _queue_id}), actor or "sin identificar",
                                    note, started)).lastrowid
        purpose = f"diagnostic:{audit_id}"
        result = {"status": "interrupted", "stage": "authorization", "ean": ean, "queue_id": _queue_id,
                  "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                  "build_commit": build_commit}
        try:
            if not approved or not actor or not note:
                raise ValueError("Responsable, motivo y aprobación explícita obligatorios")
            if len(build_commit) != 40 or any(c not in "0123456789abcdef" for c in build_commit):
                raise ValueError("Commit válido de 40 caracteres requerido")
            ean = normalize_gtin(ean)
            result["ean"] = ean
            result["stage"] = "lock_and_scope"
            if _held_lock is not None and (not isinstance(_held_lock, FileLock) or not _held_lock.is_locked or
                                           Path(_held_lock.lock_file).resolve() !=
                                           Path(f"{self.store.database_path}.continuity.lock").resolve()):
                raise ValueError("Bloqueo de continuidad no acreditado")
            with nullcontext() if _held_lock else self.continuity_lock():
                row = continuity_row(self.store)
                if not row or row["status"] != "active":
                    raise ValueError("La continuidad no está activa")
                require_private_database(self.store.database_path)
                body_limit, quota, _ = limits()
                with self.store._connect() as con, con:
                    expire(con, self.clock())
                    used = con.execute("""SELECT COALESCE(SUM(length(artifact)),0)
                        FROM price_diagnostic_requests""").fetchone()[0]
                if not has_capacity(self.store.database_path, used, body_limit, quota):
                    raise ValueError("Capacidad insuficiente para conservar la respuesta")
                frozen = json.loads(row["manifest"])
                if competitor_id not in frozen["competitors"] or ean not in frozen["eans"]:
                    raise ValueError("Par fuera del manifiesto de continuidad")
                batch = self._batch or WatchBatch(self.store)
                locator = batch.locator
                config = locator.search.config(competitor_id, initialize=False)
                domain = config["domain"]
                original_host = urlsplit(url).hostname

                def guard(target):
                    _capture_url_guard(target)
                    if urlsplit(target).hostname != original_host:
                        raise ConnectorError("Destino distinto del origen de la candidata")
                    validate_competitor_url(target, domain)

                def validate():
                    result["stage"] = "candidate_and_deferrals"
                    if _queue_id is not None:
                        with self.store._connect() as con:
                            queued = con.execute("SELECT retry_of FROM price_diagnostic_requests WHERE id=?",
                                                 (_queue_id,)).fetchone()
                            if queued and queued["retry_of"] is not None:
                                previous = con.execute("SELECT * FROM price_diagnostic_requests WHERE id=?",
                                                       (queued["retry_of"],)).fetchone()
                                if not previous:
                                    raise ValueError("Intento anterior inexistente")
                                self._diagnostic_retry_guard(con, previous)
                    self._validate_diagnostic_pair(row["id"], competitor_id, ean, url)

                scope = pilot_scope(self.store)
                legacy = scope and competitor_id in scope["competitors"] and ean in scope["eans"]
                context = (PricePilot(self.store).watch_check([(competitor_id, ean)], approved=approved)
                           if legacy else nullcontext(None))
                error = None
                fetched = None
                with context as pilot_run_id:
                    try:
                        with self.execution(0, competitor_id, ean, program="continuity", program_id=row["id"]):
                            fetched = locator.capture_diagnostic(competitor_id, ean, url, purpose=purpose,
                                                                 url_guard=guard, validate=validate,
                                                                 pilot_run_id=pilot_run_id, max_bytes=body_limit)
                    except (ValueError, RuntimeError, Timeout) as exc:
                        error = exc
                    if pilot_run_id is not None:
                        with self.store._connect() as con, con:
                            con.execute("""UPDATE pilot_jobs SET status='done',outcome=?,detail=?,checked_at=?
                                WHERE run_id=? AND competitor_id=? AND ean=?""",
                                        ("diagnostic_rejected" if error else "diagnostic_fetch",
                                         f"Captura diagnóstica: auditoría {audit_id}", self.clock().isoformat(),
                                         pilot_run_id, competitor_id, ean))
                if error is not None:
                    raise error
                if fetched is None:
                    raise RuntimeError("La consulta diagnóstica no devolvió respuesta")
                result["stage"] = "response"
                result.update(received_bytes=len(fetched.content),
                              received_sha256=hashlib.sha256(fetched.content).hexdigest(),
                              http_status=fetched.status)
                metadata, artifact = sanitise(fetched, body_limit)
                result.update({"status": "captured", "stage": "complete", "captured_at": self.clock().isoformat(),
                               **metadata, "html_base64": base64.b64encode(artifact).decode("ascii")})
                return result
        except (ValueError, RuntimeError, Timeout) as exc:
            result.update(status="rejected", error_type=type(exc).__name__)
            from price_web_fetch import RobotsDenied
            if isinstance(exc, (EvidenceError, DiagnosticRetryError, RobotsDenied)):
                result["reason"] = str(exc)
            elif str(exc) in {"La respuesta supera el tamaño máximo permitido",
                              "Capacidad insuficiente para conservar la respuesta"}:
                result["reason"] = str(exc)
            from price_access import AccessDenied
            if isinstance(exc, AccessDenied):
                result["access_code"] = exc.decision.code
                result["reason"] = exc.decision.message
            raise
        finally:
            with self.store._connect() as con, con:
                requests = [dict(r) for r in con.execute("""SELECT id,result,reserved_at FROM access_requests
                    WHERE purpose=? ORDER BY id""", (purpose,))]
                result["requests"] = requests
                audit_result = {k: v for k, v in result.items() if k != "html_base64"}
                con.execute("UPDATE access_audit SET current=? WHERE id=?",
                            (json.dumps(audit_result, ensure_ascii=False), audit_id))

    def reevaluate_restricted_pairs(self, *, actor: str = "", note: str = "",
                                    apply: bool = False) -> list[dict]:
        if apply and (not actor.strip() or not note.strip()):
            raise ValueError("Responsable y motivo obligatorios para aplicar la reevaluación")
        with self.continuity_lock():
            continuity = continuity_row(self.store)
            if not continuity or continuity["status"] != "active":
                raise ValueError("La reevaluación solo está disponible con la continuidad activa")
            with self.store._connect() as con:
                pairs = [dict(pair) for pair in con.execute("""SELECT * FROM price_continuity_pairs
                    WHERE continuity_id=? AND state='restricted' ORDER BY competitor_id,ean""",
                                                            (continuity["id"],))]
            if not pairs:
                return []
            from price_batch import WatchBatch

            batch = self._batch or WatchBatch(self.store)
            proposals = []
            evaluated_at = self.clock().isoformat()
            for pair in pairs:
                with self.execution(0, pair["competitor_id"], pair["ean"],
                                    program="continuity", program_id=continuity["id"]):
                    rows = batch.preview([pair["ean"]], [pair["competitor_id"]]).to_dict("records")
                if len(rows) != 1:
                    raise ValueError(f"No se pudo previsualizar el par {pair['competitor_id']}/{pair['ean']}")
                preview = rows[0]
                candidate_count = int(preview.get("candidate_count") or 0)
                ready = bool(preview.get("ready")) and candidate_count > 0
                if ready:
                    reason = "Candidato local y permiso vigente; queda pendiente la consulta supervisada."
                elif not candidate_count:
                    reason = (preview.get("candidate_reason") or preview.get("defer_reason")
                              or preview.get("access") or "No hay candidata local ejecutable.")
                else:
                    reason = (preview.get("defer_reason") or preview.get("access")
                              or "El par sigue restringido por su cola o por una revisión pendiente.")
                proposals.append({
                    "id": pair["id"], "competitor_id": pair["competitor_id"], "ean": pair["ean"],
                    "eligible": ready, "applied": False, "reason": str(reason),
                    "candidate_urls": [item["url"] for item in preview.get("local_candidates", [])
                                       if item.get("url")],
                    "next_check_at": evaluated_at if ready else preview.get("next_check_at"),
                    "previous_reason": pair["defer_reason"],
                })
            eligible = [proposal for proposal in proposals if proposal["eligible"]]
            if apply and eligible:
                now = self.clock().isoformat()
                recheck_reason = "Reevaluación auditada; consulta posterior sujeta a permisos y presupuesto."
                changed = []
                with self.store._connect() as con, con:
                    for proposal in eligible:
                        updated = con.execute("""UPDATE price_continuity_pairs
                            SET state='pending',next_check_at=?,defer_reason=?
                            WHERE id=? AND continuity_id=? AND state='restricted'""",
                                              (now, recheck_reason, proposal["id"], continuity["id"])).rowcount
                        if updated:
                            changed.append(proposal)
                    if changed:
                        previous = [{"id": item["id"], "competitor_id": item["competitor_id"],
                                     "ean": item["ean"], "state": "restricted",
                                     "defer_reason": item["previous_reason"]} for item in changed]
                        current = [{"id": item["id"], "competitor_id": item["competitor_id"],
                                    "ean": item["ean"], "state": "pending", "next_check_at": now,
                                    "candidate_urls": item["candidate_urls"]} for item in changed]
                        con.execute("""INSERT INTO access_audit(scope,scope_id,action,previous,current,actor,note,at)
                            VALUES('price_continuity',?,'reevaluate_restricted_pairs',?,?,?,?,?)""",
                                    (str(continuity["id"]), json.dumps(previous, ensure_ascii=False),
                                     json.dumps(current, ensure_ascii=False), actor.strip(), note.strip(), now))
                changed_ids = {item["id"] for item in changed}
                for proposal in proposals:
                    if proposal["id"] in changed_ids:
                        proposal["applied"] = True
                        proposal["reason"] = recheck_reason
                        proposal["next_check_at"] = now
            return proposals

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
                    service.diagnostic_tick()
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
