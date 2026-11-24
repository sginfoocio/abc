"""Administrator-only isolated pilot; never updates Compose or production services."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid


class PilotError(RuntimeError):
    pass


def run(stage: str, command: list[str], *, data: bytes | None = None, timeout: int = 30) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise PilotError(f"{stage}: exit {result.returncode}; salida privada no publicada")
    return result.stdout


def pilot_environment(config: dict, actor: str, authorization_id: str,
                      native_confirmation_id: str | None = None) -> dict[str, str]:
    environment = config["services"]["abcd-app"]["environment"]
    expected = {
        "PRESTASHOP_MYSQL_USER": "ps_user_vytria_lect", "PRESTASHOP_MYSQL_DATABASE": "ps_vytria",
        "PRESTASHOP_MYSQL_PREFIX": "ps_", "PRESTASHOP_MYSQL_SSL_MODE": "REQUIRED",
        "PRESTASHOP_PRICE_SHOP_ID": "1", "PRESTASHOP_PRICE_COUNTRY_ID": "6",
        "PRESTASHOP_PRICE_CURRENCY_ID": "1", "PRESTASHOP_PRICE_GROUP_ID": "1",
        "PRESTASHOP_PRICE_ENABLED": "0",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise PilotError("El contexto efectivo de Compose no coincide con el piloto autorizado")
    if not environment.get("PRESTASHOP_MYSQL_HOST") or not environment.get("PRESTASHOP_MYSQL_PASSWORD"):
        raise PilotError("Falta la conexion efectiva resuelta por Compose")
    if native_confirmation_id is None and environment.get("PRESTASHOP_PRICE_CODE_AUDITED") != "1":
        raise PilotError("Bloqueado: falta acreditar overrides/modulos PHP; CODE_AUDITED no es 1")
    if not actor.strip() or not authorization_id.strip():
        raise PilotError("Falta actor o referencia de autorizacion")
    selected = {key: value for key, value in environment.items() if key.startswith("PRESTASHOP_")}
    if any(not isinstance(value, str) for value in selected.values()):
        raise PilotError("Hay opciones PRESTASHOP sin valor de texto efectivo")
    selected["PRESTASHOP_MYSQL_ACCOUNT_POLICY"] = "authorized_broad"
    selected["PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION"] = json.dumps({
        "id": authorization_id, "actor": actor, "at": datetime.now(timezone.utc).isoformat(),
        "host": selected["PRESTASHOP_MYSQL_HOST"],
        "port": int(selected.get("PRESTASHOP_MYSQL_PORT", "3306")),
        "user": selected["PRESTASHOP_MYSQL_USER"], "database": selected["PRESTASHOP_MYSQL_DATABASE"],
    })
    if native_confirmation_id is not None:
        if not native_confirmation_id.strip():
            raise PilotError("Falta referencia de confirmacion comercial del contrato nativo")
        selected["PRESTASHOP_PRICE_VALIDATION_MODE"] = "native_rules_confirmed"
        selected["PRESTASHOP_PRICE_NATIVE_CONFIRMATION"] = json.dumps({
            "id": native_confirmation_id, "actor": actor, "at": datetime.now(timezone.utc).isoformat(),
            "account_authorization_id": authorization_id, "claim": "native_prestashop_catalog_rules",
            "context": {"shop_id": 1, "group_id": 1, "country_id": 6, "currency_id": 1, "market": "ES",
                        "currency": "EUR", "customer_id": 0, "quantity": 1, "tax_included": True,
                        "shipping_included": False, "cart_discount": False},
        })
    selected["PRESTASHOP_PRICE_ENABLED"] = "1"  # Only the isolated process receives this override.
    return selected


SNAPSHOT = r"""
import json, os, sqlite3, sys
eans = json.load(sys.stdin)
path = os.getenv("PRICE_COMPARATOR_DB_PATH", "/app/data/price_comparator.sqlite3")
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    active = {row[0] for row in con.execute("SELECT ean FROM watch_products WHERE active=1")}
    if active != set(eans):
        raise RuntimeError("El alcance activo no coincide con los 100 EAN")
    marks = ",".join("?" for _ in eans)
    snapshot = {}
    for table in ("products", "watch_products", "vytria_prices"):
        snapshot[table] = [dict(row) for row in con.execute(
            f"SELECT * FROM {table} WHERE ean IN ({marks})", eans)]
    print(json.dumps(snapshot))
"""


PILOT = r"""
import json, os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
payload = json.load(sys.stdin)
os.environ.update(payload["environment"])
import prestashop_prices as ps
from price_comparator import PriceComparatorStore

store = PriceComparatorStore(Path("/pilot/prices.sqlite3"))
with store._connect() as con, con:
    for table in ("products", "watch_products", "vytria_prices"):
        rows = payload["snapshot"][table]
        if not rows:
            continue
        allowed = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        columns = [key for key in rows[0] if key in allowed]
        names = ",".join(f'"{column}"' for column in columns)
        marks = ",".join("?" for _ in columns)
        con.executemany(f"INSERT INTO {table} ({names}) VALUES ({marks})",
                        [tuple(row[column] for column in columns) for row in rows])

eans = sorted(ps.manifest_eans())
pilot = "8056597435581"
first = ps.refresh(store._connect, [pilot], lambda: datetime.now(timezone.utc))
with store._connect() as con:
    first_row = dict(con.execute("SELECT * FROM prestashop_prices WHERE ean=?", (pilot,)).fetchone())
if first["issues"]:
    print(json.dumps({"status": "blocked", "stage": "first_ean", "ean": pilot,
                      "issue": first_row["issue"], "production_activated": False}))
    sys.exit(1)
additional = "8056597684446"
second = ps.refresh(store._connect, [additional], lambda: datetime.now(timezone.utc))
with store._connect() as con:
    second_row = dict(con.execute("SELECT * FROM prestashop_prices WHERE ean=?", (additional,)).fetchone())
if second["issues"] or not second_row["payload"]:
    print(json.dumps({"status": "blocked", "stage": "product_7861", "issue": second_row["issue"],
                      "production_activated": False}))
    sys.exit(1)
second_price = json.loads(second_row["payload"])
if (second_price["product_id"], second_price["combination_id"]) != (7861, 8425):
    raise RuntimeError("La identidad del caso adicional 7861 cambió")
remaining = [ean for ean in eans if ean not in (pilot, additional)]
for start in range(0, len(remaining), 25):
    ps.refresh(store._connect, remaining[start:start + 25], lambda: datetime.now(timezone.utc))

with sqlite3.connect("file:/pilot/prices.sqlite3?mode=ro", uri=True) as con:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    rows = [dict(row) for row in con.execute("SELECT * FROM prestashop_prices ORDER BY ean")]
    history = con.execute("SELECT COUNT(*) FROM prestashop_price_history").fetchone()[0]
    duplicate_history = con.execute(
        "SELECT COUNT(*) FROM (SELECT ean,payload FROM prestashop_price_history "
        "GROUP BY ean,payload HAVING COUNT(*)>1)").fetchone()[0]
good = [row for row in rows if ps.usable(row, datetime.now(timezone.utc))]
prices = [json.loads(row["payload"]) for row in good]
dashboard, _ = store.dashboard()
comparable = dashboard.set_index("ean")
source_correct = bool(good) and all(comparable.loc[row["ean"], "own_price_eur"] == float(price["price"])
                     and comparable.loc[row["ean"], "native_price_source"] == ps.SOURCE
                     for row, price in zip(good, prices))
odoo_preserved = bool(payload["snapshot"]["vytria_prices"])
with store._connect() as con:
    for reference in payload["snapshot"]["vytria_prices"]:
        current = dict(con.execute("SELECT * FROM vytria_prices WHERE ean=?",
                                   (reference["ean"],)).fetchone())
        odoo_preserved = odoo_preserved and all(current[key] == value for key, value in reference.items())
security_verified = bool(prices) and all(price["connection_security"].get("transaction_read_only") is True
                        and price["connection_security"].get("tls_required") is True for price in prices)
code_links = sum(not price.get("price_reference") for price in prices)
explicit_references = sum(bool(price.get("price_reference")) for price in prices)
correct = (len(good) == 100 and source_correct and odoo_preserved and security_verified
           and code_links == 95 and explicit_references == 5 and duplicate_history == 0)
report = {
    "status": "correct" if correct else "blocked", "candidate_sha": payload["commit"],
    "production_activated": False, "ean_pilot": pilot,
    "pilot_price": json.loads(first_row["payload"])["price"],
    "pilot_offer": json.loads(first_row["payload"])["offer"],
    "pilot_discount_trace": json.loads(first_row["payload"]).get("discount_trace"),
    "price_validation": json.loads(first_row["payload"]).get("price_validation"),
    "product_7861_price": second_price["price"], "product_7861_offer": second_price["offer"],
    "product_7861_discount_trace": second_price.get("discount_trace"),
    "connection_security": json.loads(first_row["payload"])["connection_security"],
    "calculated_and_saved": len(good), "issues": len(eans) - len(good),
    "code_links": code_links, "explicit_price_references": explicit_references,
    "association_mix_verified": code_links == 95 and explicit_references == 5,
    "price_issues": [{"ean": row["ean"], "issue": row["issue"] or "Lectura sin vigencia acreditada"}
                     for row in rows if row not in good],
    "inactive": sum(price.get("active") is False for price in prices),
    "without_stock": sum(price.get("in_stock") is False for price in prices),
    "history_rows": history, "duplicate_history_rows": duplicate_history,
    "comparator_source_verified": source_correct, "odoo_reference_preserved": odoo_preserved,
}
Path("/pilot/report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report))
sys.exit(0 if correct else 1)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--native-confirmation-id",
                        help="Explicit responsible confirmation of native catalog rules; not a PHP audit")
    args = parser.parse_args()
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise PilotError("Ejecutar como administrador; no cambiar permisos del Compose")
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise PilotError("SHA candidato invalido")
    git = ["git", "-C", str(args.checkout)]
    head = run("checkout", [*git, "rev-parse", "HEAD"]).decode().strip()
    if head != args.commit or run("checkout limpio", [*git, "status", "--porcelain"]):
        raise PilotError("Se necesita un checkout limpio del SHA candidato exacto")
    config = json.loads(run("Compose config", [
        "docker", "compose", "--project-directory", "/etc/cloud-production",
        "--project-name", "cloud-nas", "-f", "/etc/cloud-production/compose.private.json",
        "config", "--format", "json",
    ]))
    environment = pilot_environment(config, args.actor, args.authorization_id, args.native_confirmation_id)
    names = run("fuentes", [*git, "ls-tree", "--name-only", args.commit]).decode().splitlines()
    sources = [name for name in names if name.endswith(".py")]
    sources += ["price_trial_manifest.json"]
    archive = run("git archive", [*git, "archive", "--format=tar", args.commit, *sources])
    manifest = json.loads(run("manifiesto", [*git, "show", f"{args.commit}:price_trial_manifest.json"]))
    if len(manifest["eans"]) != 100 or len(set(manifest["eans"])) != 100:
        raise PilotError("Manifiesto no valido")
    snapshot = json.loads(run("snapshot SELECT", ["docker", "exec", "-i", "abcd-control",
                                                "python", "-c", SNAPSHOT],
                              data=json.dumps(manifest["eans"]).encode()))
    image = run("imagen instalada", ["docker", "inspect", "--format", "{{.Image}}",
                                     "abcd-control"]).decode().strip()
    base = Path("/var/lib/cloud-production-validation")
    if base.is_symlink():
        raise PilotError("El directorio de evidencia no puede ser un enlace")
    base.mkdir(mode=0o700, exist_ok=True)
    if base.stat().st_uid != 0 or base.stat().st_mode & 0o077:
        raise PilotError("El directorio de evidencia debe ser privado del administrador")
    output = base / f"vytria-{args.commit[:12]}-{uuid.uuid4().hex[:12]}"
    output.mkdir(mode=0o700)
    os.chown(output, 1037, 100)
    container = None
    with tempfile.TemporaryDirectory(prefix="vytria-source-") as directory:
        source = Path(directory)
        source.chmod(0o755)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for name in sources:
                member = tar.getmember(name)
                if not member.isfile():
                    raise PilotError("La fuente candidata no es un archivo regular")
                content = tar.extractfile(member)
                if content is None:
                    raise PilotError("No se puede extraer la fuente candidata")
                (source / name).write_bytes(content.read())
                (source / name).chmod(0o644)
        try:
            container = run("crear piloto", [
                "docker", "create", "-i", "--network", "bridge", "--read-only", "--user", "1037:100",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--memory", "768m", "--cpus", "1", "--log-driver", "none",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                "--mount", f"type=bind,src={source},dst=/candidate,readonly",
                "--mount", f"type=bind,src={output},dst=/pilot",
                "--workdir", "/candidate", "--entrypoint", "python",
                "-e", "PYTHONDONTWRITEBYTECODE=1", image, "-c", PILOT,
            ]).decode().strip()
            result = subprocess.run(["docker", "start", "-a", "-i", container],
                                    input=json.dumps({"environment": environment, "snapshot": snapshot,
                                                      "commit": args.commit}).encode(),
                                    capture_output=True, timeout=240)
            if result.stdout:
                try:
                    report = json.loads(result.stdout)
                except ValueError:
                    raise PilotError("El piloto no devolvio un informe JSON; salida privada no publicada") from None
                print(json.dumps(report))
            print(json.dumps({"private_evidence": str(output), "dependency_image": image}))
            if result.returncode:
                raise PilotError(f"Piloto: exit {result.returncode}; stderr privado no publicado")
        finally:
            if container is not None:
                run("retirar solo piloto", ["docker", "rm", "-f", container])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PilotError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        raise SystemExit(1) from None
