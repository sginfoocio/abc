"""Watch list of the scalable price-watch engine: which catalogue products are monitored and how often.

Three ways to add products, all auditable and reversible (removal only deactivates):

* individual selection from the comparator catalogue (``products``);
* bulk import of EAN (pasted text or a file with an ``EAN`` column);
* explicit rules by brand, category, model prefix or text, applied on demand.

Only products with a complete commercial identity (brand + model + colour) can be watched, because
that is what the locator compares. The calibre and a published EAN are not required.
Monitoring frequency: the group's ``interval_hours`` when set, otherwise the competitor's interval in
``competitor_schedule`` (24 h by default). The supervised pilot does not use this list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Callable, Iterable

import pandas as pd

from price_comparator import PriceComparatorStore, normalize_gtin

DEFAULT_GROUP = "General"
SOURCES = {"manual": "Selección individual", "import": "Importación masiva", "rule": "Regla"}
RULE_FIELDS = ("brand", "category", "model_prefix", "text_contains")
MAX_BULK = 20_000


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class WatchReport:
    added: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{len(self.added)} añadidos · {len(self.reactivated)} reactivados · "
                f"{len(self.already)} ya vigilados · {len(self.rejected)} rechazados")


def parse_eans(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Normalised, de-duplicated EAN from free text (lines, commas, semicolons, tabs or spaces)."""
    valid: list[str] = []
    rejected: list[tuple[str, str]] = []
    for token in re.split(r"[\s,;]+", text or ""):
        if not token:
            continue
        try:
            ean = normalize_gtin(token)
        except ValueError as exc:
            rejected.append((token, str(exc)))
            continue
        if ean not in valid:
            valid.append(ean)
    return valid, rejected


def eans_from_frame(frame: pd.DataFrame) -> tuple[list[str], list[tuple[str, str]]]:
    column = next((name for name in frame.columns if str(name).strip().upper() in {"EAN", "GTIN", "EAN13"}), None)
    if column is None:
        raise ValueError("El fichero debe tener una columna «EAN»")
    values = frame[column].dropna().astype(str).str.strip().str.removesuffix(".0")
    return parse_eans("\n".join(values))


class PriceWatch:
    def __init__(self, store: PriceComparatorStore, *, clock: Callable[[], datetime] = _utc) -> None:
        self.store = store
        self._now = clock

    # groups -----------------------------------------------------------------------------------
    def ensure_defaults(self) -> None:
        with self.store._connect() as connection, connection:
            connection.execute("INSERT OR IGNORE INTO watch_groups(name,interval_hours,note,created_at) "
                               "VALUES(?,NULL,'Frecuencia del competidor',?)", (DEFAULT_GROUP, _iso(self._now())))

    def groups(self) -> pd.DataFrame:
        self.ensure_defaults()
        with self.store._connect() as connection:
            return pd.read_sql_query("""SELECT g.name,g.interval_hours,g.note,
                (SELECT COUNT(*) FROM watch_products w WHERE w.group_name=g.name AND w.active=1) AS products
                FROM watch_groups g ORDER BY g.name""", connection)

    def save_group(self, name: str, interval_hours: float | None, note: str = "") -> None:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("Nombre de grupo obligatorio (máx. 60 caracteres)")
        if interval_hours is not None and not 1 <= interval_hours <= 24 * 30:
            raise ValueError("La frecuencia debe estar entre 1 hora y 30 días")
        with self.store._connect() as connection, connection:
            connection.execute("""INSERT INTO watch_groups(name,interval_hours,note,created_at) VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET interval_hours=excluded.interval_hours,note=excluded.note""",
                               (name, interval_hours, note.strip(), _iso(self._now())))

    def monitor_interval_hours(self, ean: str, competitor_id: str) -> float | None:
        """Group frequency if set, else the competitor's; ``None`` when the product is not watched."""
        with self.store._connect() as connection:
            row = connection.execute("""SELECT g.interval_hours AS group_hours,s.interval_hours AS competitor_hours
                FROM watch_products w JOIN watch_groups g ON g.name=w.group_name
                LEFT JOIN competitor_schedule s ON s.competitor_id=?
                WHERE w.ean=? AND w.active=1""", (competitor_id, normalize_gtin(ean))).fetchone()
        if row is None:
            return None
        return row["group_hours"] or row["competitor_hours"] or 24.0

    # watch list -------------------------------------------------------------------------------
    def add(self, eans: Iterable[str], *, actor: str, group: str = DEFAULT_GROUP, source: str = "manual",
            rule_id: int | None = None) -> WatchReport:
        if source not in SOURCES:
            raise ValueError("Origen no válido")
        if not actor.strip():
            raise ValueError("Indique quién añade los productos")
        self.ensure_defaults()
        report = WatchReport()
        candidates = list(eans)
        if len(candidates) > MAX_BULK:
            raise ValueError(f"Máximo {MAX_BULK} EAN por operación")
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            if connection.execute("SELECT 1 FROM watch_groups WHERE name=?", (group,)).fetchone() is None:
                raise ValueError(f"Grupo inexistente: {group}")
            for raw in candidates:
                try:
                    ean = normalize_gtin(raw)
                except ValueError as exc:
                    report.rejected.append((str(raw), str(exc)))
                    continue
                product = connection.execute("SELECT brand,model,color FROM products WHERE ean=?", (ean,)).fetchone()
                if product is None:
                    report.rejected.append((ean, "No está en el catálogo del comparador"))
                    continue
                missing = [label for label, value in (("marca", product["brand"]), ("modelo", product["model"]),
                                                      ("color", product["color"])) if not str(value).strip()]
                if missing:
                    report.rejected.append((ean, "Identidad incompleta: falta " + ", ".join(missing)))
                    continue
                current = connection.execute("SELECT active FROM watch_products WHERE ean=?", (ean,)).fetchone()
                if current is not None and current["active"]:
                    report.already.append(ean)
                    continue
                connection.execute("""INSERT INTO watch_products(ean,group_name,source,rule_id,active,added_by,added_at)
                    VALUES(?,?,?,?,1,?,?) ON CONFLICT(ean) DO UPDATE SET group_name=excluded.group_name,
                    source=excluded.source,rule_id=excluded.rule_id,active=1,added_by=excluded.added_by,
                    added_at=excluded.added_at,removed_by='',removed_at=NULL""",
                                   (ean, group, source, rule_id, actor.strip(), now))
                (report.reactivated if current is not None else report.added).append(ean)
        return report

    def remove(self, eans: Iterable[str], *, actor: str) -> int:
        if not actor.strip():
            raise ValueError("Indique quién retira los productos")
        now = _iso(self._now())
        with self.store._connect() as connection, connection:
            return sum(connection.execute("""UPDATE watch_products SET active=0,removed_by=?,removed_at=?
                WHERE ean=? AND active=1""", (actor.strip(), now, normalize_gtin(ean))).rowcount for ean in eans)

    def move(self, eans: Iterable[str], group: str) -> int:
        with self.store._connect() as connection, connection:
            if connection.execute("SELECT 1 FROM watch_groups WHERE name=?", (group,)).fetchone() is None:
                raise ValueError(f"Grupo inexistente: {group}")
            return sum(connection.execute("UPDATE watch_products SET group_name=? WHERE ean=? AND active=1",
                                          (group, normalize_gtin(ean))).rowcount for ean in eans)

    def watchlist(self, *, include_inactive: bool = False) -> pd.DataFrame:
        query = """SELECT w.ean,p.brand,p.model,p.color,p.commercial_name,p.category,w.group_name,w.source,
                          r.name AS rule_name,w.active,w.added_by,w.added_at,w.removed_by,w.removed_at
                   FROM watch_products w JOIN products p ON p.ean=w.ean LEFT JOIN watch_rules r ON r.id=w.rule_id"""
        if not include_inactive:
            query += " WHERE w.active=1"
        with self.store._connect() as connection:
            return pd.read_sql_query(query + " ORDER BY p.brand,p.model,p.color", connection)

    def catalog(self) -> pd.DataFrame:
        """Catalogue products with their watch state (for individual selection)."""
        with self.store._connect() as connection:
            frame = pd.read_sql_query("""SELECT p.ean,p.brand,p.model,p.color,p.commercial_name,p.category,
                COALESCE(w.active,0) AS watched FROM products p LEFT JOIN watch_products w ON w.ean=p.ean
                ORDER BY p.brand,p.model,p.color""", connection)
        frame["complete"] = (frame[["brand", "model", "color"]].fillna("").apply(
            lambda column: column.astype(str).str.strip() != "")).all(axis=1)
        return frame

    def counts(self) -> dict[str, int]:
        with self.store._connect() as connection:
            row = connection.execute("""SELECT COUNT(*) AS total,SUM(source='manual') AS manual,
                SUM(source='import') AS import_,SUM(source='rule') AS rule FROM watch_products WHERE active=1""").fetchone()
            catalog = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        return {"watched": row["total"] or 0, "manual": row["manual"] or 0, "import": row["import_"] or 0,
                "rule": row["rule"] or 0, "catalog": catalog}

    # rules ------------------------------------------------------------------------------------
    def save_rule(self, name: str, *, actor: str, group: str = DEFAULT_GROUP, brand: str = "", category: str = "",
                  model_prefix: str = "", text_contains: str = "", active: bool = True) -> int:
        name = name.strip()
        criteria = {"brand": brand.strip(), "category": category.strip(), "model_prefix": model_prefix.strip(),
                    "text_contains": text_contains.strip()}
        if not name:
            raise ValueError("La regla necesita un nombre")
        if not any(criteria.values()):
            raise ValueError("La regla necesita al menos un criterio (marca, categoría, modelo o texto)")
        if not actor.strip():
            raise ValueError("Indique quién crea la regla")
        self.ensure_defaults()
        with self.store._connect() as connection, connection:
            if connection.execute("SELECT 1 FROM watch_groups WHERE name=?", (group,)).fetchone() is None:
                raise ValueError(f"Grupo inexistente: {group}")
            connection.execute("""INSERT INTO watch_rules(name,brand,category,model_prefix,text_contains,group_name,
                active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                brand=excluded.brand,category=excluded.category,model_prefix=excluded.model_prefix,
                text_contains=excluded.text_contains,group_name=excluded.group_name,active=excluded.active""",
                               (name, *criteria.values(), group, int(active), actor.strip(), _iso(self._now())))
            return connection.execute("SELECT id FROM watch_rules WHERE name=?", (name,)).fetchone()[0]

    def rules(self) -> pd.DataFrame:
        with self.store._connect() as connection:
            return pd.read_sql_query("SELECT * FROM watch_rules ORDER BY name", connection)

    def preview_rule(self, *, brand: str = "", category: str = "", model_prefix: str = "",
                     text_contains: str = "") -> pd.DataFrame:
        """Catalogue products matching the criteria (case-insensitive). Read-only."""
        frame = self.catalog()
        mask = pd.Series(True, index=frame.index)
        if brand.strip():
            mask &= frame["brand"].str.strip().str.casefold() == brand.strip().casefold()
        if category.strip():
            mask &= frame["category"].str.strip().str.casefold() == category.strip().casefold()
        if model_prefix.strip():
            mask &= frame["model"].str.strip().str.casefold().str.startswith(model_prefix.strip().casefold())
        if text_contains.strip():
            needle = text_contains.strip().casefold()
            mask &= (frame["commercial_name"].str.casefold().str.contains(needle, regex=False)
                     | frame["model"].str.casefold().str.contains(needle, regex=False))
        return frame[mask].reset_index(drop=True)

    def apply_rules(self, *, actor: str, rule_ids: Iterable[int] | None = None) -> dict[str, WatchReport]:
        """Add every catalogue match of the active rules. Explicit: products are never removed by a rule."""
        selected = self.rules()
        selected = selected[selected["active"] == 1]
        if rule_ids is not None:
            selected = selected[selected["id"].isin(list(rule_ids))]
        reports: dict[str, WatchReport] = {}
        for rule in selected.to_dict("records"):
            matches = self.preview_rule(**{name: rule[name] for name in RULE_FIELDS})
            report = self.add(matches["ean"], actor=actor, group=rule["group_name"], source="rule",
                              rule_id=rule["id"])
            with self.store._connect() as connection, connection:
                connection.execute("UPDATE watch_rules SET last_applied_at=?,last_matched=? WHERE id=?",
                                   (_iso(self._now()), len(matches), rule["id"]))
            reports[rule["name"]] = report
        return reports
