"""Persistent GIO work queue, consumed by the existing Cloud provider worker."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
from html import escape
from itertools import groupby
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time as clock
from urllib.parse import urljoin, urlsplit

from filelock import FileLock, Timeout
import requests
from sqlalchemy import text

from adyen_reconciliation import ReportError
from gio_reports import (
    CENTRES, MADRID, MAX_REPORT_BYTES, DownloadResponseError, Group, candidate_csv,
    daily_group, daily_groups, previous_day,
)
from kering_images import ConfigStore, atomic_write


LOGGER = logging.getLogger(__name__)
PORTAL_URL = "https://opticadiagonal-gioweb.deipe.com/"
PORTAL_HOST = "opticadiagonal-gioweb.deipe.com"
REPORT_ID = 57
REPORT_TYPE = "4"
REPORT_TIME_LIMIT = 210
TERMINAL = {"blocked", "ready", "candidate", "no_movements", "download_error", "internal_error"}
STATES = {
    "pending": "Pendiente", "downloading": "Descargando", "downloaded": "Descargado",
    "validating": "Validando", "validated": "Validado", "candidate": "Pendiente de validar Odoo",
    "ready": "CSV validado", "no_movements": "Sin movimientos", "blocked": "Incidencias: CSV bloqueado",
    "download_error": "Error de descarga", "internal_error": "Error interno",
}
DEFAULTS = {
    "enabled": False, "daily_time": "07:00", "recipients": [], "send_enabled": False,
    "portal_url": PORTAL_URL, "username": "", "password": "",
    "last_connection_status": "not_checked", "last_connection_check": "",
    "operations_approved": False,
    "odoo_import_evidence": "", "business_evidence": "",
    "mapping": {"company": 0, "journals": dict.fromkeys(CENTRES, 0),
                "accounts": dict.fromkeys(("43000000", "47700000", "70000001", "70000002", "70000005"), 0)},
}
ERRORS = {
    "portal_not_verified": "Acceso al portal GIO rechazado; revise credenciales y permisos.",
    "transient_download": "Fallo transitorio de descarga; reintentos limitados a tres.",
    "internal_error": "Error interno; revise almacenamiento y el codigo del evento con soporte.",
    "mail_uncertain": "Resultado de envio incierto. Compruebe Elementos enviados antes de autorizar un reenvio.",
}


def data_root() -> Path:
    return Path(os.getenv("GIO_DATA_ROOT", str(Path(__file__).parent / "masterdata_data" / "gio")))


def admin(role: str | None, actor: str) -> None:
    if role != "admin" or not actor.strip():
        raise PermissionError("GIO requiere un administrador identificado")


def validate_mapping(mapping: dict) -> None:
    values = [mapping["company"], *(mapping["journals"][c] for c in CENTRES),
              *(mapping["accounts"][c] for c in DEFAULTS["mapping"]["accounts"])]
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Configure IDs tecnicos positivos de sociedad, diarios y cuentas Odoo")


class GioConfigStore(ConfigStore):
    """Same encrypted-file/key mechanism as provider configuration, separate namespace."""

    def load(self) -> dict:
        path = self.root / "config.enc"
        result = json.loads(json.dumps(DEFAULTS))
        if path.exists():
            result.update(json.loads(self.cipher().decrypt(path.read_bytes())))
        result["enabled"] = False
        result["send_enabled"] = False
        return result

    def save(self, config: dict) -> None:
        config = {**DEFAULTS, **config}
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config["daily_time"]):
            raise ValueError("La hora debe tener formato HH:MM (Europe/Madrid)")
        config["portal_url"] = validate_portal_url(config["portal_url"])
        if not isinstance(config["recipients"], list) or any(
            not isinstance(value, str) or not re.fullmatch(r"[^@\s;,<>]+@[^@\s;,<>]+\.[^@\s;,<>]+", value)
            for value in config["recipients"]
        ):
            raise ValueError("Destinatarios no validos")
        if config["send_enabled"]:
            raise ValueError("Envios reales bloqueados hasta validar el funcionamiento completo de GIO")
        if config["enabled"]:
            raise ValueError("Automatizacion bloqueada: falta verificar el adaptador autenticado GIO")
        if config["odoo_import_evidence"].strip():
            validate_mapping(config["mapping"])
        if config["operations_approved"] and not config["business_evidence"].strip():
            raise ValueError("Documente la aprobacion de operaciones y referencias")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(self.root / "config.enc", self.cipher().encrypt(json.dumps(config).encode()))


class GioConnectionError(Exception):
    pass


class PortalNotVerified(Exception):
    pass


class TransientDownload(Exception):
    """A confirmed network or server failure eligible for a bounded retry."""


def validate_portal_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("La URL GIO debe ser el portal HTTPS oficial")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ValueError("La URL GIO debe ser el portal HTTPS oficial") from None
    if (parsed.scheme != "https" or parsed.hostname != PORTAL_HOST or port is not None
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("La URL GIO debe ser el portal HTTPS oficial")
    return PORTAL_URL


def _authenticated_gio_session(config: dict) -> requests.Session:
    username = config.get("username", "")
    password = config.get("password", "")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        raise GioConnectionError("Guarde el usuario y la contraseña de GIO antes de probar la conexión.")
    username = username.strip()

    portal_url = validate_portal_url(config.get("portal_url", PORTAL_URL))
    session = requests.Session()
    try:
        try:
            response = session.get(portal_url, timeout=(5, 15), allow_redirects=False)
        except requests.Timeout:
            raise TransientDownload() from None
        except requests.RequestException:
            raise TransientDownload() from None

        if response.is_redirect:
            destination = urljoin(portal_url, response.headers.get("Location", ""))
            try:
                target = urlsplit(destination)
                target_port = target.port
            except ValueError:
                raise GioConnectionError("El portal GIO redirigió fuera del dominio autorizado.") from None
            if (target.scheme != "https" or target.hostname != PORTAL_HOST or target_port is not None
                    or target.username or target.password):
                raise GioConnectionError("El portal GIO redirigió fuera del dominio autorizado.")
            try:
                response = session.get(destination, timeout=(5, 15), allow_redirects=False)
            except requests.Timeout:
                raise TransientDownload() from None
            except requests.RequestException:
                raise TransientDownload() from None

        if response.status_code != 200 or "gioweb_login.js" not in response.text:
            raise GioConnectionError("La respuesta no corresponde a la pantalla de acceso GIO esperada.")

        try:
            response = session.post(
                urljoin(PORTAL_URL, "login_check"),
                data={"_username": username, "_password": password, "idioma": "Español"},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise TransientDownload() from None
        except requests.RequestException:
            raise TransientDownload() from None

        if response.status_code != 200:
            raise GioConnectionError(
                f"El portal GIO devolvió HTTP {response.status_code} al comprobar el acceso."
            )
        try:
            result = response.json()
        except ValueError:
            raise GioConnectionError("GIO devolvió una respuesta de acceso no reconocida.") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            raise GioConnectionError("GIO rechazó las credenciales o requiere una acción adicional.")
    except (GioConnectionError, TransientDownload):
        session.close()
        raise
    return session


def check_gio_connection(config: dict) -> None:
    try:
        with _authenticated_gio_session(config):
            pass
    except TransientDownload:
        raise GioConnectionError("No se pudo completar la comprobación del acceso GIO.") from None


class GioReportDownloader:
    """Fetch the observed all-stores official report without registering a portal session."""

    def fetch(self, day: date, config: dict) -> bytes:
        try:
            session = _authenticated_gio_session(config)
        except GioConnectionError as exc:
            raise PortalNotVerified(str(exc)) from None

        criteria = {
            "id": REPORT_ID,
            "fecha_ini": day.isoformat(),
            "fecha_fin": day.isoformat(),
            "id_tienda": None,
            "tipo_listado_oficial": REPORT_TYPE,
        }
        try:
            try:
                response = session.get(
                    urljoin(PORTAL_URL, "Informesinformesestadisticos/descargar"),
                    params={
                        "id": str(REPORT_ID),
                        "criterios": json.dumps(criteria, separators=(",", ":")),
                        "timeLimit": str(REPORT_TIME_LIMIT),
                        "_dc": str(int(clock.time() * 1000)),
                    },
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": PORTAL_URL + "#listados_oficiales",
                    },
                    timeout=(10, 60),
                    stream=True,
                )
            except requests.Timeout:
                raise TransientDownload() from None
            except requests.RequestException:
                raise TransientDownload() from None

            try:
                if response.status_code == 429 or response.status_code >= 500:
                    raise TransientDownload()
                if response.status_code in {401, 403}:
                    raise PortalNotVerified("El portal GIO no autorizó la descarga del informe.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if response.status_code != 200 or content_type not in {"application/csv", "text/csv"}:
                    raise DownloadResponseError("GIO no devolvió el CSV oficial esperado.")
                content = bytearray()
                for block in response.iter_content(64 * 1024):
                    if block:
                        content.extend(block)
                        if len(content) > MAX_REPORT_BYTES:
                            raise DownloadResponseError("El informe supera el límite de 15 MiB.")
                if not content:
                    raise DownloadResponseError("GIO devolvió un CSV vacío.")
                return bytes(content)
            except requests.RequestException:
                raise TransientDownload() from None
            finally:
                response.close()
        finally:
            session.close()


class GioStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = root / "history.sqlite3"
        with FileLock(root / "schema.lock", timeout=10), self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    day TEXT PRIMARY KEY, created REAL NOT NULL, origin TEXT NOT NULL,
                    mail_state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
                    mail_detail TEXT NOT NULL DEFAULT '', mail_manifest TEXT NOT NULL DEFAULT '',
                    original TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS centres (
                    day TEXT NOT NULL REFERENCES batches(day), centre TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, original TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
                    PRIMARY KEY(day,centre)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, day TEXT NOT NULL, centre TEXT NOT NULL,
                    stage TEXT NOT NULL, detail TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    day TEXT NOT NULL, centre TEXT NOT NULL, kind TEXT NOT NULL,
                    digest TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(day,centre,kind,digest)
                );
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(batches)")}
            if "original" not in columns:
                conn.execute("ALTER TABLE batches ADD COLUMN original TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.database, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def lock(self):
        return FileLock(self.root / "worker.lock", timeout=0)

    def event(self, day, centre, stage, detail="", actor="worker"):
        with self.connect() as conn:
            conn.execute("INSERT INTO events(day,centre,stage,detail,actor,created) VALUES(?,?,?,?,?,?)",
                         (day, centre, stage, detail, actor, clock.time()))

    def history(self, day: str | None = None) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT c.*,b.mail_state,b.mail_detail FROM centres c JOIN batches b USING(day) "
                + ("WHERE day=? " if day else "") + "ORDER BY day DESC,centre LIMIT 300",
                (day,) if day else ())]

    def batch(self, day: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE day=?", (day,)).fetchone()
        return dict(row) if row else None

    def events(self, day: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM events WHERE day=? ORDER BY id DESC LIMIT 300", (day,))]

    def update(self, day: str, centre: str, **values):
        allowed = {"state", "attempts", "next_attempt", "original", "result", "summary", "error"}
        if not values.keys() <= allowed:
            raise ValueError("Campo de estado desconocido")
        with self.connect() as conn:
            conn.execute("UPDATE centres SET " + ",".join(f"{key}=?" for key in values) +
                         ",updated=? WHERE day=? AND centre=?",
                         (*values.values(), clock.time(), day, centre))
            for kind in ("original", "result"):
                if values.get(kind):
                    conn.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)",
                                 (day, centre, kind, values[kind], clock.time()))

    def artifacts(self, day: str, centre: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM artifacts WHERE day=? AND centre IN (?, '') ORDER BY created DESC",
                (day, centre))]

    def batch_original(self, day: str) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT original FROM batches WHERE day=?", (day,)).fetchone()
        return row["original"] if row else ""

    def set_batch_original(self, day: str, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        with self.connect() as conn:
            row = conn.execute("SELECT original FROM batches WHERE day=?", (day,)).fetchone()
            if not row:
                raise ValueError("Fecha sin lote GIO")
            if row["original"] and row["original"] != digest:
                raise ValueError("El original general de esta fecha es inmutable")
        self.save_blob(content)
        with self.connect() as conn:
            row = conn.execute("SELECT original FROM batches WHERE day=?", (day,)).fetchone()
            if row["original"] and row["original"] != digest:
                raise ValueError("El original general de esta fecha es inmutable")
            conn.execute("UPDATE batches SET original=? WHERE day=?", (digest, day))
            conn.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)",
                         (day, "", "original_general", digest, clock.time()))
        return digest

    def enqueue(self, day: date, centres: list[str], *, role: str, actor: str,
                send: bool = False, originals: dict[str, bytes] | None = None):
        admin(role, actor)
        if not centres or not set(centres) <= CENTRES.keys():
            raise ValueError("Seleccione centros validos")
        if originals and (not originals.keys() <= set(centres) or
                          any(not content or len(content) > MAX_REPORT_BYTES for content in originals.values())):
            raise ValueError("Originales fuera de alcance, vacios o demasiado grandes")
        with self.lock():
            self.enqueue_locked(day, centres, actor=actor, send=send, originals=originals)

    def enqueue_locked(self, day: date, centres: list[str], *, actor: str, send=False, originals=None):
        key = day.isoformat()
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO batches(day,created,origin,mail_state) VALUES(?,?,?,?)",
                         (key, clock.time(), actor, "pending" if send else "draft"))
        for centre in dict.fromkeys(centres):
            with self.connect() as conn:
                present = conn.execute("SELECT 1 FROM centres WHERE day=? AND centre=?", (key, centre)).fetchone()
            if present:
                continue
            digest = ""
            if originals and centre in originals:
                digest = self.save_blob(originals[centre])
            with self.connect() as conn:
                conn.execute("INSERT INTO centres(day,centre,original,updated) VALUES(?,?,?,?)",
                             (key, centre, digest, clock.time()))
            if digest:
                self.update(key, centre, original=digest)
            self.event(key, centre, "queued", "offline" if digest else "portal", actor)

    def save_blob(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.root / "files"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / digest
        if not path.exists():
            atomic_write(path, content)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise OSError("El archivo persistente no coincide con su huella")
        return digest

    def blob(self, digest: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Identificador de archivo no valido")
        content = (self.root / "files" / digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise OSError("Integridad de archivo incorrecta")
        return content

    def download(self, digest: str, *, role: str, actor: str) -> bytes:
        admin(role, actor)
        return self.blob(digest)

    def retry(self, day: str, centre: str, *, role: str, actor: str, original: bytes | None = None):
        admin(role, actor)
        if original is not None and (not original or len(original) > MAX_REPORT_BYTES):
            raise ValueError("Original corregido vacio o demasiado grande")
        with self.lock(), self.connect() as conn:
            row = conn.execute("SELECT * FROM centres WHERE day=? AND centre=?", (day, centre)).fetchone()
            if not row or row["state"] not in TERMINAL:
                raise ValueError("No hay un resultado final que reintentar")
            conn.execute("UPDATE centres SET state='pending',attempts=0,next_attempt=0,error='',result='',updated=? "
                         "WHERE day=? AND centre=?", (clock.time(), day, centre))
            conn.execute("INSERT INTO events(day,centre,stage,detail,actor,created) VALUES(?,?,?,?,?,?)",
                         (day, centre, "retry", "Reutiliza original; no reenvia correo", actor, clock.time()))
            if original is not None:
                digest = self.save_blob(original)
                conn.execute("UPDATE centres SET original=? WHERE day=? AND centre=?", (digest, day, centre))
                conn.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)",
                             (day, centre, "original", digest, clock.time()))
                conn.execute("INSERT INTO events(day,centre,stage,detail,actor,created) VALUES(?,?,?,?,?,?)",
                             (day, centre, "corrected_original", digest, actor, clock.time()))

    def request_send(self, day: str, *, role: str, actor: str, confirm_duplicate_risk=False):
        admin(role, actor)
        with self.lock(), self.connect() as conn:
            batch = conn.execute("SELECT * FROM batches WHERE day=?", (day,)).fetchone()
            if not batch:
                raise ValueError("Fecha sin ejecuciones")
            if batch["mail_state"] in {"accepted", "uncertain", "sending"} and not confirm_duplicate_risk:
                raise ValueError("Compruebe Elementos enviados y confirme el riesgo de duplicado")
            conn.execute("UPDATE batches SET mail_state='pending',generation=generation+1,mail_detail='' WHERE day=?",
                         (day,))
            conn.execute("INSERT INTO events(day,centre,stage,detail,actor,created) VALUES(?,?,?,?,?,?)",
                         (day, "", "send_requested", "Confirmacion explicita", actor, clock.time()))


def release_allowed(config: dict) -> bool:
    return bool(config["operations_approved"] and config["business_evidence"].strip()
                and config["odoo_import_evidence"].strip())


def contract_digest(config: dict) -> str:
    contract = {key: config[key] for key in
                ("mapping", "operations_approved", "business_evidence", "odoo_import_evidence")}
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def current_result(job: dict, config: dict) -> bool:
    return bool(job["state"] == "ready" and job["result"] and release_allowed(config)
                and json.loads(job["summary"]).get("contract") == contract_digest(config))


def prepare_mail_payload(store: GioStore, batch: dict, config: dict, *,
                         include_candidates=False, require_terminal=False) -> dict:
    day = batch["day"]
    with store.connect() as conn:
        jobs = [dict(row) for row in conn.execute("SELECT * FROM centres WHERE day=? ORDER BY centre", (day,))]
    terminal = bool(jobs) and all(job["state"] in TERMINAL for job in jobs)
    if require_terminal and not terminal:
        return {"terminal": False}
    attachments = []
    statuses = []
    complete = set(job["centre"] for job in jobs) == set(CENTRES)
    if batch.get("original"):
        attachments.append((f"GIO-{day}-original-general.csv", store.blob(batch["original"])))
    for job in jobs:
        centre, state = job["centre"], job["state"]
        if state not in {"ready", "no_movements"}:
            complete = False
        summary = json.loads(job["summary"])
        totals = "; ".join(f"{key}={value}" for key, value in summary.get("totals", {}).items())
        issues = summary.get("issues", [])
        incidents = "; ".join(
            f"fila {issue.get('row', '?')}: "
            f"{issue['operation'] + '; ' if issue.get('operation') else ''}"
            f"{issue['code']} ({issue.get('difference', '-')})"
            for issue in issues[:10])
        if summary.get("totals_complete") is False:
            totals = "TOTALES INCOMPLETOS, NO UTILIZABLES: " + totals
        statuses.append(f"{CENTRES[centre][0]}: {STATES[state]}; filas={summary.get('rows', '-')}; "
                        f"{totals}; {job['error']}; incidencias={len(issues)} {incidents}")
        if job["original"] and state == "download_error":
            statuses.append("Respuesta web rechazada conservada en Cloud, no adjunta como informe.")
        elif job["original"] and job["original"] != batch.get("original"):
            attachments.append((f"GIO-{day}-{centre}-original.csv", store.blob(job["original"])))
        if current_result(job, config):
            attachments.append((f"GIO-{day}-{centre}-odoo.csv", store.blob(job["result"])))
        elif (include_candidates and state == "candidate" and job["result"]
              and summary.get("contract") == contract_digest(config)):
            attachments.append((f"GIO-{day}-{centre}-odoo-candidato.csv", store.blob(job["result"])))
            statuses.append(
                f"{CENTRES[centre][0]}: CSV candidato adjunto solo para revision; importador Odoo no validado.")
        elif state == "ready":
            complete = False
            statuses.append("CSV retenido: aprobaciones de configuracion retiradas")
    for centre in CENTRES:
        if centre not in {job["centre"] for job in jobs}:
            statuses.append(f"{CENTRES[centre][0]}: no solicitado en esta fecha")
    label = "COMPLETO" if complete else "PARCIAL / REVISION"
    subject = f"GIO {day} - {label} - envio {batch['generation']}"
    body = "<p>" + "</p><p>".join(escape(value) for value in statuses) + "</p>"
    body += "<p>Aceptacion por Graph no acredita entrega. CSV solo para importacion manual, nunca publicados en Odoo.</p>"
    if include_candidates:
        body += "<p>VISTA PREVIA: no se envia. Los CSV candidatos no deben importarse hasta validar el importador.</p>"
    return {
        "subject": subject,
        "body": body,
        "recipients": list(config["recipients"]),
        "attachments": attachments,
        "complete": complete,
        "terminal": terminal,
    }


def verify_odoo_mapping(engine, mapping: dict) -> dict:
    """Schema/identity check only. Never calls load/create/write or an import."""
    validate_mapping(mapping)
    if engine.dialect.name != "postgresql":
        raise ValueError("La comprobacion real requiere la conexion PostgreSQL Odoo")
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            version = conn.execute(text(
                "SELECT latest_version FROM ir_module_module WHERE name='base'")).scalar()
            if not isinstance(version, str) or not version.startswith("15.0."):
                raise ValueError("Version Odoo distinta del contrato 15; revise el formato de importacion")
            for centre, (_, name, _) in CENTRES.items():
                row = conn.execute(text("SELECT name,company_id FROM account_journal WHERE id=:id"),
                                   {"id": mapping["journals"][centre]}).mappings().one_or_none()
                if not row or row["name"] != name or row["company_id"] != mapping["company"]:
                    raise ValueError("Un diario Odoo no coincide con el centro o la sociedad")
            for code, account_id in mapping["accounts"].items():
                row = conn.execute(text("SELECT code,company_id FROM account_account WHERE id=:id"),
                                   {"id": account_id}).mappings().one_or_none()
                if not row or row["code"] != code or row["company_id"] != mapping["company"]:
                    raise ValueError("Una cuenta Odoo no coincide con el codigo o la sociedad")
            currency = conn.execute(text(
                "SELECT c.name FROM res_company p JOIN res_currency c ON c.id=p.currency_id WHERE p.id=:company"),
                {"company": mapping["company"]}).scalar()
            if currency != "EUR":
                raise ValueError("La sociedad Odoo no utiliza EUR")
            fields = {(row["model"], row["name"]): (row["ttype"], row["relation"])
                      for row in conn.execute(text(
                          "SELECT model,name,ttype,relation FROM ir_model_fields "
                          "WHERE model IN ('account.move','account.move.line')")).mappings()}
            required = {
                ("account.move", "date"): ("date", None),
                ("account.move", "journal_id"): ("many2one", "account.journal"),
                ("account.move", "ref"): ("char", None),
                ("account.move", "move_type"): ("selection", None),
                ("account.move", "line_ids"): ("one2many", "account.move.line"),
                ("account.move.line", "account_id"): ("many2one", "account.account"),
                ("account.move.line", "name"): ("char", None),
                ("account.move.line", "debit"): ("monetary", None),
                ("account.move.line", "credit"): ("monetary", None),
            }
            if any(fields.get(key) != value for key, value in required.items()):
                raise ValueError("El esquema Odoo difiere del contrato de campos")
            return {"version": version, "company_verified": True, "currency": currency,
                    "checked_at": datetime.now(timezone.utc).isoformat(), "import_tested": False}
        finally:
            transaction.rollback()


class GioWorker:
    def __init__(self, store: GioStore, config_store: GioConfigStore, downloader=None, sender=None, odoo_checker=None):
        self.store, self.config_store = store, config_store
        self.downloader = downloader or GioReportDownloader()
        if sender is None:
            from graph_mail_downloader import send_alert_email
            sender = send_alert_email
        self.sender = sender
        self.odoo_checker = odoo_checker

    def tick(self, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Se requiere zona horaria")
        try:
            with self.store.lock():
                config = self.config_store.load()
                # The lock guarantees no live sender can own a stale 'sending' state.
                with self.store.connect() as conn:
                    conn.execute("UPDATE batches SET mail_state='uncertain',mail_detail=? WHERE mail_state='sending'",
                                 (ERRORS["mail_uncertain"],))
                local = now.astimezone(MADRID)
                due = time.fromisoformat(config["daily_time"])
                if config["enabled"] and local.time().replace(tzinfo=None) >= due:
                    self.store.enqueue_locked(previous_day(now), list(CENTRES), actor="automatic",
                                              send=config["send_enabled"])
                with self.store.connect() as conn:
                    jobs = [dict(row) for row in conn.execute(
                        "SELECT * FROM centres WHERE state NOT IN "
                        "('blocked','ready','candidate','no_movements','download_error','internal_error') "
                        "AND next_attempt<=? ORDER BY day,centre LIMIT 30", (now.timestamp(),))]
                for _, grouped_jobs in groupby(jobs, key=lambda job: job["day"]):
                    self.process_day(list(grouped_jobs), config, now.timestamp())
                with self.store.connect() as conn:
                    batches = [dict(row) for row in conn.execute("SELECT * FROM batches WHERE mail_state='pending'")]
                for batch in batches:
                    self.send(batch, config)
        except Timeout:
            return "busy"
        return "idle"

    def process_day(self, jobs: list[dict], config: dict, timestamp: float):
        if not jobs:
            return
        store, day = self.store, jobs[0]["day"]
        portal_jobs = [job for job in jobs if not job["original"]]
        general_content = None
        general_digest = store.batch_original(day) if portal_jobs else ""

        def fail_download(state, error, stage, attempts=None, next_attempt=None):
            for job in portal_jobs:
                values = {"state": state, "error": error}
                if attempts is not None:
                    values["attempts"] = attempts
                if next_attempt is not None:
                    values["next_attempt"] = next_attempt
                store.update(day, job["centre"], **values)
                store.event(day, job["centre"], stage)

        if portal_jobs:
            attempts = max(job["attempts"] for job in portal_jobs) + 1
            try:
                if general_digest:
                    general_content = store.blob(general_digest)
                else:
                    for job in portal_jobs:
                        store.update(day, job["centre"], state="downloading", attempts=attempts)
                    general_content = self.downloader.fetch(date.fromisoformat(day), config)
                    if not general_content or len(general_content) > MAX_REPORT_BYTES:
                        raise DownloadResponseError("Descarga vacia o demasiado grande")
                    general_digest = store.set_batch_original(day, general_content)
                    store.event(day, "", "downloaded_general", general_digest)
            except TransientDownload:
                state = "download_error" if attempts >= 3 else "pending"
                fail_download(state, ERRORS["transient_download"],
                              "download_error" if attempts >= 3 else "download_retry",
                              attempts=attempts, next_attempt=timestamp + 60 * attempts)
                general_content = None
            except PortalNotVerified as exc:
                fail_download("download_error", str(exc) or ERRORS["portal_not_verified"],
                              "portal_not_verified", attempts=attempts)
                general_content = None
            except DownloadResponseError as exc:
                fail_download("download_error", str(exc), "rejected_web_response", attempts=attempts)
                general_content = None
            except Exception as exc:
                LOGGER.error("gio stage=download day=%s exception_type=%s", day, type(exc).__name__)
                fail_download("internal_error", ERRORS["internal_error"], "internal_error", attempts=attempts)
                general_content = None

        groups, encoding = None, None
        if general_content is not None:
            try:
                groups, encoding = daily_groups(general_content, date.fromisoformat(day))
            except DownloadResponseError as exc:
                fail_download("download_error", str(exc), "rejected_web_response")
                general_content = None
            except ReportError as exc:
                fail_download("blocked", str(exc), "blocked")
                general_content = None

        for job in jobs:
            try:
                if job["original"]:
                    content = store.blob(job["original"])
                    self.process_centre(job, config, content, job["original"])
                elif general_content is not None:
                    self.process_centre(job, config, general_content, general_digest, groups, encoding)
            except Exception as exc:
                LOGGER.error("gio stage=source day=%s centre=%s exception_type=%s",
                             day, job["centre"], type(exc).__name__)
                store.update(day, job["centre"], state="internal_error",
                             error=ERRORS["internal_error"], result="")
                store.event(day, job["centre"], "internal_error", type(exc).__name__)

    def process_centre(self, job: dict, config: dict, content: bytes, source_digest: str,
                       groups: dict | None = None, encoding: str | None = None):
        store, day, centre = self.store, job["day"], job["centre"]
        try:
            summary = json.loads(job["summary"])
            if job["state"] == "validated" and summary.get("source_sha256") == source_digest:
                group = Group(date.fromisoformat(day), centre, rows=summary["rows"],
                              totals={key: Decimal(value) for key, value in summary["totals"].items()},
                              issues=summary["issues"])
            else:
                store.update(day, centre, state="validating")
                if groups is None:
                    group, encoding = daily_group(content, date.fromisoformat(day), centre)
                else:
                    group = groups[centre]
                summary = {**group.summary(), "encoding": encoding, "source_sha256": source_digest}
                store.update(day, centre, state=group.status, summary=json.dumps(summary), error="", result="")
                store.event(day, centre, group.status, json.dumps(group.issues))
            if group.status in {"blocked", "no_movements"}:
                return
            summary["contract"] = contract_digest(config)
            try:
                validate_mapping(config["mapping"])
            except (ValueError, KeyError):
                store.update(day, centre, state="candidate", error="Falta configurar los IDs tecnicos Odoo")
                return
            result = candidate_csv(group, config["mapping"])
            digest = store.save_blob(result)
            allowed = release_allowed(config) and self.odoo_checker is not None
            if allowed:
                try:
                    summary["odoo_schema"] = self.odoo_checker(config["mapping"])
                except Exception as exc:
                    LOGGER.error("gio stage=odoo_contract day=%s centre=%s exception_type=%s",
                                 day, centre, type(exc).__name__)
                    store.update(day, centre, result=digest, state="candidate",
                                 error="No se pudo verificar el contrato Odoo; revise esquema, IDs y conexion")
                    store.event(day, centre, "odoo_contract_failed", type(exc).__name__)
                    return
            store.update(day, centre, result=digest, state="ready" if allowed else "candidate",
                         summary=json.dumps(summary),
                         error="" if allowed else "CSV candidato: faltan aprobaciones o comprobacion del esquema Odoo")
            store.event(day, centre, "generated", json.dumps({
                "sha256": digest, "released": allowed,
                "odoo_schema_checked": bool(summary.get("odoo_schema")),
            }))
        except PortalNotVerified:
            store.update(day, centre, state="download_error", error=ERRORS["portal_not_verified"])
            store.event(day, centre, "portal_not_verified")
        except DownloadResponseError as exc:
            store.update(day, centre, state="download_error", error=str(exc), result="")
            store.event(day, centre, "rejected_web_response", str(exc))
        except ReportError as exc:
            store.update(day, centre, state="blocked", error=str(exc), result="")
            store.event(day, centre, "blocked", str(exc))
        except Exception as exc:
            LOGGER.error("gio stage=processing day=%s centre=%s exception_type=%s", day, centre, type(exc).__name__)
            store.update(day, centre, state="internal_error", error=ERRORS["internal_error"], result="")
            store.event(day, centre, "internal_error", type(exc).__name__)

    def send(self, batch: dict, config: dict):
        try:
            self.send_prepared(batch, config)
        except (OSError, ValueError, KeyError) as exc:
            LOGGER.error("gio stage=mail_prepare day=%s exception_type=%s", batch["day"], type(exc).__name__)
            self.mail_state(batch["day"], "blocked", "No se pueden preparar los adjuntos; revise integridad y almacenamiento")
            self.store.event(batch["day"], "", "mail_blocked", type(exc).__name__)

    def send_prepared(self, batch: dict, config: dict):
        day = batch["day"]
        if not config["send_enabled"] or not config["recipients"]:
            return
        payload = prepare_mail_payload(self.store, batch, config, require_terminal=True)
        if not payload["terminal"]:
            return
        attachments = payload["attachments"]
        subject, body = payload["subject"], payload["body"]
        manifest = json.dumps({"recipients": payload["recipients"], "subject": subject,
                               "files": [(name, hashlib.sha256(content).hexdigest()) for name, content in attachments]})
        # Fail before marking 'sending' for the known Graph simple-message size limit.
        if sum(4 * ((len(content) + 2) // 3) for _, content in attachments) + len(body.encode()) > 2_500_000:
            self.mail_state(day, "blocked", "Adjuntos demasiado grandes para envio simple Graph; descarguelos desde Cloud.")
            self.store.event(day, "", "mail_blocked", "attachment_size")
            return
        with self.store.connect() as conn:
            conn.execute("UPDATE batches SET mail_state='sending',mail_manifest=? WHERE day=?", (manifest, day))
        self.store.event(day, "", "sending", manifest)
        try:
            self.sender(subject, body, payload["recipients"], attachments=attachments)
        except Exception as exc:
            LOGGER.error("gio stage=mail day=%s exception_type=%s", day, type(exc).__name__)
            self.mail_state(day, "uncertain", ERRORS["mail_uncertain"])
            self.store.event(day, "", "mail_uncertain", type(exc).__name__)
            return
        self.mail_state(day, "accepted", "Aceptado por el servicio de correo; entrega no confirmada")
        self.store.event(day, "", "mail_accepted")

    def mail_state(self, day: str, state: str, detail: str):
        with self.store.connect() as conn:
            conn.execute("UPDATE batches SET mail_state=?,mail_detail=? WHERE day=?", (state, detail, day))


def tick(engine_factory=None):
    """Called from the existing provider scheduler, independently of Kering failures."""
    root = data_root()
    checker = (lambda mapping: verify_odoo_mapping(engine_factory(), mapping)) if engine_factory else None
    return GioWorker(GioStore(root), GioConfigStore(root), odoo_checker=checker).tick()
