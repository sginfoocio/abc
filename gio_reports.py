"""Lossless GIO input validation and provisional, balanced Odoo move exports."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
import re
from zoneinfo import ZoneInfo

from adyen_reconciliation import ReportError


MADRID = ZoneInfo("Europe/Madrid")
CENTRES = {
    "0": ("Ourense", "VENTAS OU", "70000001"),
    "1": ("Vigo", "VENTAS VI", "70000002"),
    "2": ("Coru\u00f1a", "VENTAS CO", "70000005"),
}
COLUMNS = tuple((
    "FECHA;TIENDA;OPERACION;NUM_FACTURA;NUM_ENCARGO;NOMBRE_CLIENTE;CODIGO_CLIENTE;"
    "NIF_CLIENTE;FORMA_PAGO;BIMPONIBLE_10;CUOTA_10;TOTAL_10;BIMPONIBLE_21;CUOTA_21;"
    "TOTAL_21;TOTAL;ORIGINALES;PAIS_NIF;MODO_PAGO;ENCARGOS"
).split(";"))
AMOUNTS = ("BIMPONIBLE_10", "CUOTA_10", "TOTAL_10", "BIMPONIBLE_21", "CUOTA_21", "TOTAL_21", "TOTAL")
OPERATIONS = {
    "F.SIMPLIFICADA", "FACTURA", "F.SIMPL.RECTIFICATIVA S1",
    "F.SIMPL.RECTIFICATIVA S2", "RECTIFICATIVA",
}
MAX_REPORT_BYTES = 15 * 1024 * 1024
ODOO_COLUMNS = (
    "id", "date", "journal_id/.id", "move_type", "ref",
    "line_ids/account_id/.id", "line_ids/name", "line_ids/debit", "line_ids/credit",
)


class DownloadResponseError(ReportError):
    """The supposed report is an authentication/web response, not accounting data."""


def previous_day(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("Se requiere una fecha con zona horaria")
    return now.astimezone(MADRID).date() - timedelta(days=1)


def spanish_money(value: str) -> Decimal:
    value = value.strip().removesuffix("\u20ac").strip()
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)?(?:,\d{1,2})?", value) or not re.search(r"\d", value):
        raise ReportError("Importe decimal espanol invalido")
    return Decimal(value.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))


@dataclass
class Group:
    day: date
    centre: str
    rows: int = 0
    totals: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(AMOUNTS, Decimal("0.00")))
    issues: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "blocked" if self.issues else ("validated" if self.rows else "no_movements")

    def summary(self) -> dict:
        complete = not any(issue["code"] in {"numero_columnas", "importe_invalido"} for issue in self.issues)
        return {"rows": self.rows, "totals": {k: str(v) for k, v in self.totals.items()},
                "totals_complete": complete, "issues": self.issues}


@dataclass
class Report:
    encoding: str
    groups: dict[tuple[date, str], Group]


def read_report(content: bytes) -> Report:
    if not content or len(content) > MAX_REPORT_BYTES:
        raise ReportError("Informe vacio o superior al limite de 15 MiB")
    try:
        text = content.decode("utf-8-sig")
        encoding = "utf-8-sig" if content.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError as exc:
            raise ReportError("Codificacion no reconocida") from exc
    if "<html" in text[:2048].lower() or "<!doctype" in text[:2048].lower():
        raise DownloadResponseError("La descarga es HTML; revise acceso o sesion GIO")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        raise ReportError("El informe contiene caracteres de control")
    reader = csv.reader(StringIO(text, newline=""), delimiter=";", strict=True)
    groups: dict[tuple[date, str], Group] = {}
    try:
        if tuple(next(reader, ())) != COLUMNS:
            raise ReportError("Cabecera o separador distintos del contrato GIO (20 columnas, punto y coma)")
        for values in reader:
            number = reader.line_num
            if not values:
                continue
            if len(values) < 2:
                raise ReportError(f"Fila {number}: no permite identificar fecha y centro")
            row = dict(zip(COLUMNS, values))
            try:
                day = datetime.strptime(row["FECHA"], "%d/%m/%Y").date()
                if day.strftime("%d/%m/%Y") != row["FECHA"] or row["TIENDA"] not in CENTRES:
                    raise ValueError()
            except ValueError as exc:
                raise ReportError(f"Fila {number}: fecha o tienda invalida") from exc
            group = groups.setdefault((day, row["TIENDA"]), Group(day, row["TIENDA"]))
            group.rows += 1
            operation = row["OPERACION"].strip()
            if len(values) != len(COLUMNS):
                group.issues.append({"row": number, "code": "numero_columnas",
                                     "operation": operation, "expected": len(COLUMNS), "actual": len(values)})
                continue
            if row["OPERACION"] not in OPERATIONS:
                group.issues.append({"row": number, "code": "operacion_no_reconocida",
                                     "operation": operation})
            amounts = {}
            for name in AMOUNTS:
                try:
                    amounts[name] = spanish_money(row[name])
                except ReportError:
                    group.issues.append({"row": number, "code": "importe_invalido",
                                         "operation": operation, "field": name})
            if len(amounts) != len(AMOUNTS):
                continue
            for name, amount in amounts.items():
                group.totals[name] += amount
            for rate in ("10", "21"):
                delta = amounts[f"TOTAL_{rate}"] - amounts[f"BIMPONIBLE_{rate}"] - amounts[f"CUOTA_{rate}"]
                if delta:
                    group.issues.append({"row": number, "code": f"descuadre_iva_{rate}",
                                         "operation": operation, "difference": str(delta)})
            delta = amounts["TOTAL"] - amounts["TOTAL_10"] - amounts["TOTAL_21"]
            if delta:
                group.issues.append({"row": number, "code": "descuadre_total",
                                     "operation": operation, "difference": str(delta)})
    except csv.Error as exc:
        raise ReportError(f"CSV mal formado cerca de la fila {reader.line_num}") from exc
    return Report(encoding, groups)


def daily_group(content: bytes, day: date, centre: str) -> tuple[Group, str]:
    if centre not in CENTRES:
        raise ReportError("Centro desconocido")
    report = read_report(content)
    if set(report.groups) - {(day, centre)}:
        raise ReportError("La descarga contiene fechas o centros fuera de los filtros solicitados")
    return report.groups.get((day, centre), Group(day, centre)), report.encoding


def daily_groups(content: bytes, day: date) -> tuple[dict[str, Group], str]:
    report = read_report(content)
    if {report_day for report_day, _ in report.groups} - {day}:
        raise ReportError("La descarga contiene fechas fuera del filtro solicitado")
    return ({centre: report.groups.get((day, centre), Group(day, centre)) for centre in CENTRES},
            report.encoding)


def accounting_lines(group: Group) -> list[dict]:
    if group.issues:
        raise ReportError("Centro y fecha bloqueados por incidencias")
    if not group.rows:
        return []
    totals = group.totals
    sales = CENTRES[group.centre][2]
    definitions = [
        ("43000000", "Clientes", totals["TOTAL"]),
        ("47700000", "IVA repercutido 21%", -totals["CUOTA_21"]),
        ("47700000", "IVA repercutido 10%", -totals["CUOTA_10"]),
        (sales, "Ventas 21%", -totals["BIMPONIBLE_21"]),
        (sales, "Ventas 10%", -totals["BIMPONIBLE_10"]),
    ]
    if sum((value for _, _, value in definitions), Decimal(0)):
        raise ReportError("El asiento agregado no esta equilibrado")
    return [{"account": account, "label": label, "debit": max(value, Decimal(0)),
             "credit": max(-value, Decimal(0))} for account, label, value in definitions]


def candidate_csv(group: Group, mapping: dict) -> bytes:
    """One move, with continuation lines; no Odoo sequence or invented invoice number."""
    lines = accounting_lines(group)
    if not lines:
        raise ReportError("Sin movimientos: no se genera asiento")
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(ODOO_COLUMNS)
    for index, line in enumerate(lines):
        parent = [
            f"gio_{group.day.isoformat().replace('-', '')}_{group.centre}",
            group.day.isoformat(), mapping["journals"][group.centre], "entry",
            f"GIO {group.day.isoformat()} {CENTRES[group.centre][0]}",
        ] if index == 0 else [""] * 5
        writer.writerow(parent + [mapping["accounts"][line["account"]], line["label"],
                                  format(line["debit"], ".2f"), format(line["credit"], ".2f")])
    return output.getvalue().encode("utf-8-sig")
