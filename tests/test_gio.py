import base64
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import MagicMock, Mock

from cryptography.fernet import Fernet
import pytest
import requests
from streamlit.testing.v1 import AppTest

import gio_cloud as cloud
from gio_reports import (
    AMOUNTS, COLUMNS, ODOO_COLUMNS, ReportError, accounting_lines, candidate_csv,
    daily_group, previous_day, read_report, spanish_money,
)


DAY = date(2026, 10, 31)
ACTOR = {"role": "admin", "actor": "synthetic-admin"}
NOW = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)


def movement(**changes):
    row = dict.fromkeys(COLUMNS, "")
    row.update(dict.fromkeys(AMOUNTS, "0"))
    row.update(FECHA="31/10/2026", TIENDA="0", OPERACION="F.SIMPLIFICADA",
               NUM_FACTURA="SYNTHETIC", NUM_ENCARGO="SYNTHETIC", NOMBRE_CLIENTE="CLIENTE SINTETICO",
               BIMPONIBLE_10="100", CUOTA_10="10", TOTAL_10="110",
               BIMPONIBLE_21="20", CUOTA_21="4,20", TOTAL_21="24,20", TOTAL="134,20")
    row.update(changes)
    return row


def report(*rows, encoding="utf-8-sig"):
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode(encoding)


def settings():
    config = json.loads(json.dumps(cloud.DEFAULTS))
    config["mapping"] = {"company": 9, "journals": {"0": 91, "1": 92, "2": 93},
                         "accounts": dict(zip(cloud.DEFAULTS["mapping"]["accounts"], range(101, 106)))}
    config.update(operations_approved=True, business_evidence="SYNTHETIC approval",
                  odoo_import_evidence="SYNTHETIC Odoo 15 staging grouping evidence",
                  send_enabled=True, recipients=["accounting@example.test"])
    return config


class Config:
    def __init__(self, value=None):
        self.value = settings() if value is None else value

    def load(self):
        return self.value


def worker(tmp_path, config=None, downloader=None, sender=None):
    store = cloud.GioStore(tmp_path)
    return cloud.GioWorker(store, config or Config(), downloader=downloader, sender=sender or Mock(),
                           odoo_checker=lambda _: {"import_tested": False, "synthetic": True})


@pytest.mark.parametrize("value,expected", [
    (",45", "0.45"), ("-,45", "-0.45"), ("1.234,56", "1234.56"),
    (" -145,00 \u20ac ", "-145.00"), ("+10", "10.00"), ("0", "0.00"),
])
def test_spanish_decimals(value, expected):
    assert spanish_money(value) == Decimal(expected)


@pytest.mark.parametrize("value", ["", ",", "NaN", "Infinity", "0.45", "1,234.56", "1,234", "1e2", "1 234,50"])
def test_reject_ambiguous_or_invalid_amounts(value):
    with pytest.raises(ReportError):
        spanish_money(value)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-8", "cp1252"])
def test_encoding_and_legitimate_repeated_rows_are_preserved(encoding):
    content = report(movement(NOMBRE_CLIENTE="SINT\u00c9TICO"), movement(NOMBRE_CLIENTE="SINT\u00c9TICO"), encoding=encoding)
    parsed = read_report(content)
    group = parsed.groups[(DAY, "0")]
    assert parsed.encoding == encoding
    assert group.rows == 2 and group.totals["TOTAL"] == Decimal("268.40")
    assert not group.issues
    assert "SINT" not in json.dumps(group.summary())


def test_grouping_is_by_date_and_centre_not_client_or_order():
    parsed = read_report(report(
        movement(), movement(TIENDA="1"), movement(FECHA="30/10/2026"), movement()))
    assert set(parsed.groups) == {(DAY, "0"), (DAY, "1"), (date(2026, 10, 30), "0")}
    assert parsed.groups[(DAY, "0")].rows == 2
    with pytest.raises(ReportError, match="fuera"):
        daily_group(report(movement(), movement(TIENDA="1")), DAY, "0")


@pytest.mark.parametrize("content", [
    b"<html>login</html>", b"<!DOCTYPE html><body>login</body>",
    report(movement()).replace(b";", b"\t"), report(movement()).replace(b"ENCARGOS", b"NEW_COLUMN"),
    report(movement(FECHA="31/02/2026")), report(movement(TIENDA="3")),
    report(movement()).replace(b"SYNTHETIC", b"\x00", 1),
])
def test_changed_schema_html_and_invalid_scope_fail_closed(content):
    with pytest.raises(ReportError):
        read_report(content)


def test_known_1642_mismatch_blocks_entire_centre_not_other_centres():
    bad = movement(BIMPONIBLE_10="116,89", CUOTA_10="11,69", TOTAL_10="128,58",
                   BIMPONIBLE_21="0", CUOTA_21="0", TOTAL_21="0", TOTAL="145")
    parsed = read_report(report(bad, movement(), movement(TIENDA="1")))
    group = parsed.groups[(DAY, "0")]
    assert group.status == "blocked"
    assert group.issues == [{"row": 2, "code": "descuadre_total", "difference": "16.42"}]
    with pytest.raises(ReportError):
        accounting_lines(group)
    assert parsed.groups[(DAY, "1")].status == "validated"


def test_unescaped_delimiters_are_not_repaired_and_block_only_identified_group():
    malformed = report(movement()).replace(b"134,20;", b"134,20;extra;")
    group = read_report(malformed).groups[(DAY, "0")]
    assert group.status == "blocked" and group.rows == 1
    assert group.issues == [{"row": 2, "code": "numero_columnas", "expected": 20, "actual": 21}]
    assert group.totals["TOTAL"] == 0  # no guessed column alignment
    with pytest.raises(ReportError):
        candidate_csv(group, settings()["mapping"])


def test_invalid_amount_unknown_operation_and_rate_total_are_incidents():
    group = read_report(report(movement(OPERACION="UNKNOWN"), movement(CUOTA_10="bad"),
                               movement(TOTAL_21="25", TOTAL="135"))).groups[(DAY, "0")]
    assert {issue["code"] for issue in group.issues} == {
        "operacion_no_reconocida", "importe_invalido", "descuadre_iva_21"}


@pytest.mark.parametrize("operation", [
    "RECTIFICATIVA", "F.SIMPL.RECTIFICATIVA S1", "F.SIMPL.RECTIFICATIVA S2",
])
def test_refunds_keep_signs_and_reverse_debit_credit_without_dedup(operation):
    original = movement()
    refund = {**original, **{key: "-" + original[key] for key in AMOUNTS}, "OPERACION": operation}
    refund["TOTAL"] = "-67,10"
    for key in AMOUNTS:
        refund[key] = str(spanish_money(original[key]) / -2).replace(".", ",")
    group = read_report(report(original, refund)).groups[(DAY, "0")]
    lines = accounting_lines(group)
    assert group.rows == 2 and group.totals["TOTAL"] == Decimal("67.10")
    assert sum(line["debit"] for line in lines) == sum(line["credit"] for line in lines)
    negative = read_report(report(refund)).groups[(DAY, "0")]
    reversed_lines = accounting_lines(negative)
    assert reversed_lines[0]["credit"] == Decimal("67.10")
    assert reversed_lines[1]["debit"] == Decimal("2.10")


def test_export_has_one_move_five_lines_separate_vat_and_no_assigned_sequence():
    group = read_report(report(movement())).groups[(DAY, "0")]
    content = candidate_csv(group, settings()["mapping"])
    rows = list(csv.DictReader(StringIO(content.decode("utf-8-sig")), delimiter=";"))
    assert tuple(rows[0]) == ODOO_COLUMNS
    assert len(rows) == 5
    assert rows[0]["id"] == "gio_20261031_0"
    assert rows[0]["journal_id/.id"] == "91"
    assert all(not row["id"] and not row["date"] and not row["journal_id/.id"] for row in rows[1:])
    assert "name" not in rows[0] and "OZ1" not in content.decode("utf-8-sig")
    assert rows[1]["line_ids/account_id/.id"] == rows[2]["line_ids/account_id/.id"]
    assert rows[1]["line_ids/name"] != rows[2]["line_ids/name"]
    assert sum(Decimal(row["line_ids/debit"]) for row in rows) == Decimal("134.20")
    assert sum(Decimal(row["line_ids/credit"]) for row in rows) == Decimal("134.20")


@pytest.mark.parametrize("instant,expected", [
    ("2026-03-01T00:00:00+01:00", "2026-02-28"),
    ("2027-01-01T00:00:00+01:00", "2026-12-31"),
    ("2026-03-29T01:30:00+00:00", "2026-03-28"),
    ("2026-10-25T01:30:00+00:00", "2026-10-24"),
    ("2026-10-31T23:30:00+00:00", "2026-10-31"),
])
def test_previous_madrid_calendar_day_month_year_and_dst(instant, expected):
    assert previous_day(datetime.fromisoformat(instant)) == date.fromisoformat(expected)
    with pytest.raises(ValueError):
        previous_day(datetime(2026, 1, 1))


def test_empty_header_report_is_no_movements_not_failed_download(tmp_path):
    w = worker(tmp_path)
    w.store.enqueue(DAY, ["0"], originals={"0": report()}, **ACTOR)
    w.tick(NOW)
    row = w.store.history()[0]
    assert row["state"] == "no_movements" and not row["result"]
    assert w.store.blob(row["original"]) == report()
    with pytest.raises(ReportError, match="vacio"):
        daily_group(b"", DAY, "0")


def test_login_html_is_download_error_preserved_but_never_emailed_as_report(tmp_path):
    send = Mock()
    w = worker(tmp_path, sender=send)
    html = b"<!DOCTYPE html><html>login response, not a financial report</html>"
    w.store.enqueue(DAY, ["0"], send=True, originals={"0": html}, **ACTOR)
    w.tick(NOW)
    row = w.store.history()[0]
    assert row["state"] == "download_error" and w.store.blob(row["original"]) == html
    assert send.call_args.kwargs["attachments"] == []
    assert "PARCIAL" in send.call_args.args[0]


def test_partial_results_attach_originals_and_only_released_csv_once(tmp_path):
    send = Mock()
    w = worker(tmp_path, sender=send)
    w.store.enqueue(DAY, ["0", "1", "2"], send=True, originals={
        "0": report(movement()), "1": report(movement(TIENDA="1", TOTAL="145")),
    }, **ACTOR)
    w.tick(NOW)
    rows = {r["centre"]: r for r in w.store.history()}
    assert {key: r["state"] for key, r in rows.items()} == {"0": "ready", "1": "blocked", "2": "download_error"}
    assert all(r["mail_state"] == "accepted" for r in rows.values())
    assert "PARCIAL" in send.call_args.args[0]
    attachments = dict(send.call_args.kwargs["attachments"])
    assert set(attachments) == {"GIO-2026-10-31-0-original.csv", "GIO-2026-10-31-1-original.csv",
                                "GIO-2026-10-31-0-odoo.csv"}
    w = worker(tmp_path, sender=send)
    w.store.enqueue(DAY, ["0", "1", "2"], send=True, **ACTOR)
    w.tick(NOW + timedelta(minutes=5))
    assert send.call_count == 1
    assert "CLIENTE SINTETICO" not in json.dumps(w.store.events(DAY.isoformat()))


def test_transient_download_retries_bounded_per_centre_and_survive_restart(tmp_path):
    downloader = Mock()
    downloader.fetch.side_effect = cloud.TransientDownload()
    w = worker(tmp_path, downloader=downloader)
    w.store.enqueue(DAY, ["0", "1"], originals={"1": report(movement(TIENDA="1"))}, **ACTOR)
    w.tick(NOW)
    assert downloader.fetch.call_count == 1
    assert {r["centre"]: r["state"] for r in w.store.history()} == {"0": "pending", "1": "ready"}
    w.tick(NOW + timedelta(seconds=30))
    assert downloader.fetch.call_count == 1
    w = worker(tmp_path, downloader=downloader)
    w.tick(NOW + timedelta(minutes=2))
    w.tick(NOW + timedelta(minutes=5))
    w.tick(NOW + timedelta(days=1))
    assert downloader.fetch.call_count == 3
    assert {r["centre"]: r["state"] for r in w.store.history()}["0"] == "download_error"


def test_resume_validation_without_redownload_and_corrected_original_history(tmp_path):
    content = report(movement(TOTAL="145"))
    downloader = Mock()
    w = worker(tmp_path, downloader=downloader)
    w.store.enqueue(DAY, ["0"], originals={"0": content}, **ACTOR)
    w.store.update(DAY.isoformat(), "0", state="validating")
    w.tick(NOW)
    assert not downloader.fetch.called
    assert w.store.history()[0]["state"] == "blocked"
    corrected = report(movement())
    w.store.retry(DAY.isoformat(), "0", original=corrected, **ACTOR)
    w.tick(NOW)
    assert w.store.history()[0]["state"] == "ready"
    originals = [a for a in w.store.artifacts(DAY.isoformat(), "0") if a["kind"] == "original"]
    assert {w.store.blob(a["digest"]) for a in originals} == {content, corrected}
    assert w.store.history()[0]["mail_state"] == "draft"


def test_restart_after_committed_validation_resumes_generation_without_revalidating(tmp_path, monkeypatch):
    w = worker(tmp_path)
    w.store.enqueue(DAY, ["0"], originals={"0": report(movement())}, **ACTOR)
    generate = cloud.candidate_csv
    monkeypatch.setattr(cloud, "candidate_csv", Mock(side_effect=SystemExit("synthetic crash")))
    with pytest.raises(SystemExit):
        w.tick(NOW)
    assert w.store.history()[0]["state"] == "validated"
    monkeypatch.setattr(cloud, "candidate_csv", generate)
    validate = Mock(wraps=cloud.daily_group)
    monkeypatch.setattr(cloud, "daily_group", validate)
    w = worker(tmp_path)
    w.tick(NOW)
    assert w.store.history()[0]["state"] == "ready" and not validate.called


def test_candidates_are_not_emailed_or_promoted_by_merely_changing_config(tmp_path):
    config = Config()
    config.value["odoo_import_evidence"] = ""
    send = Mock()
    w = worker(tmp_path, config=config, sender=send)
    w.store.enqueue(DAY, ["0"], send=True, originals={"0": report(movement())}, **ACTOR)
    w.tick(NOW)
    assert w.store.history()[0]["state"] == "candidate"
    assert len(send.call_args.kwargs["attachments"]) == 1
    config.value["odoo_import_evidence"] = "new evidence"
    w.store.request_send(DAY.isoformat(), confirm_duplicate_risk=True, **ACTOR)
    w.tick(NOW)
    assert len(send.call_args.kwargs["attachments"]) == 1
    w.store.retry(DAY.isoformat(), "0", **ACTOR)
    w.tick(NOW)
    assert w.store.history()[0]["state"] == "ready"
    assert send.call_count == 2  # retry is not a request to send


def test_changed_contract_withholds_previously_released_file(tmp_path):
    config, send = Config(), Mock()
    w = worker(tmp_path, config=config, sender=send)
    w.store.enqueue(DAY, ["0"], originals={"0": report(movement())}, **ACTOR)
    w.tick(NOW)
    config.value["mapping"]["journals"]["0"] = 999
    w.store.request_send(DAY.isoformat(), **ACTOR)
    w.tick(NOW)
    assert len(send.call_args.kwargs["attachments"]) == 1
    assert "PARCIAL" in send.call_args.args[0]


def test_odoo_check_failure_keeps_candidate_and_never_exposes_database_exception(tmp_path):
    w = worker(tmp_path)
    w.odoo_checker = Mock(side_effect=RuntimeError("SECRET CONNECTION STRING"))
    w.store.enqueue(DAY, ["0"], originals={"0": report(movement())}, **ACTOR)
    w.tick(NOW)
    row = w.store.history()[0]
    assert row["state"] == "candidate" and row["result"]
    assert "SECRET" not in row["error"] and "SECRET" not in str(w.store.events(DAY.isoformat()))


@pytest.mark.parametrize("wrong_company", [False, True])
def test_odoo_mapping_check_uses_readonly_transaction_and_rollback(wrong_company):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    conn = engine.connect.return_value.__enter__.return_value
    queries = []
    journals = settings()["mapping"]["journals"]
    accounts = settings()["mapping"]["accounts"]
    required_fields = [
        ("account.move", "date", "date", None),
        ("account.move", "journal_id", "many2one", "account.journal"),
        ("account.move", "ref", "char", None),
        ("account.move", "move_type", "selection", None),
        ("account.move", "line_ids", "one2many", "account.move.line"),
        ("account.move.line", "account_id", "many2one", "account.account"),
        ("account.move.line", "name", "char", None),
        ("account.move.line", "debit", "monetary", None),
        ("account.move.line", "credit", "monetary", None),
    ]

    def execute(query, parameters=None):
        sql = str(query)
        queries.append(sql)
        result = Mock()
        if "ir_module_module" in sql:
            result.scalar.return_value = "15.0.1.3"
        elif "account_journal" in sql:
            centre = next(key for key, value in journals.items() if value == parameters["id"])
            result.mappings.return_value.one_or_none.return_value = {
                "name": cloud.CENTRES[centre][1], "company_id": 666 if wrong_company else 9}
        elif "account_account" in sql:
            code = next(key for key, value in accounts.items() if value == parameters["id"])
            result.mappings.return_value.one_or_none.return_value = {"code": code, "company_id": 9}
        elif "res_company" in sql:
            result.scalar.return_value = "EUR"
        elif "ir_model_fields" in sql:
            result.mappings.return_value = [
                dict(zip(("model", "name", "ttype", "relation"), row)) for row in required_fields]
        return result

    conn.execute.side_effect = execute
    if wrong_company:
        with pytest.raises(ValueError, match="sociedad"):
            cloud.verify_odoo_mapping(engine, settings()["mapping"])
    else:
        result = cloud.verify_odoo_mapping(engine, settings()["mapping"])
        assert result["version"] == "15.0.1.3" and result["import_tested"] is False
    assert queries[0] == "SET TRANSACTION READ ONLY"
    assert all(query.startswith(("SELECT", "SET")) for query in queries)
    conn.begin.return_value.rollback.assert_called_once()


def test_disabled_mail_and_oversized_attachments_are_not_sent(tmp_path):
    config, send = Config(), Mock()
    config.value["send_enabled"] = False
    w = worker(tmp_path, config, sender=send)
    w.store.enqueue(DAY, ["0"], send=True, originals={"0": report()}, **ACTOR)
    w.tick(NOW)
    assert not send.called and w.store.history()[0]["mail_state"] == "pending"
    config.value["send_enabled"] = True
    monkey_blob = w.store.blob
    w.store.blob = lambda digest: monkey_blob(digest) + b" " * 2_500_000
    w.tick(NOW)
    assert not send.called and w.store.history()[0]["mail_state"] == "blocked"
    w.tick(NOW)
    assert not send.called


@pytest.mark.parametrize("crash", [False, True])
def test_uncertain_mail_requires_explicit_audited_resend(tmp_path, crash):
    send = Mock(side_effect=requests.Timeout("SECRET RESPONSE MUST NOT BE LOGGED"))
    w = worker(tmp_path, sender=send)
    w.store.enqueue(DAY, ["0"], send=True, originals={"0": report()}, **ACTOR)
    if crash:
        with w.store.connect() as conn:
            conn.execute("UPDATE batches SET mail_state='sending'")
    w.tick(NOW)
    assert w.store.history()[0]["mail_state"] == "uncertain"
    count = send.call_count
    w.tick(NOW + timedelta(days=1))
    assert send.call_count == count
    assert "SECRET" not in str(w.store.events(DAY.isoformat()))
    with pytest.raises(ValueError, match="riesgo"):
        w.store.request_send(DAY.isoformat(), **ACTOR)
    w.store.request_send(DAY.isoformat(), confirm_duplicate_risk=True, **ACTOR)
    send.side_effect = None
    w.tick(NOW)
    assert w.store.history()[0]["mail_state"] == "accepted"
    assert send.call_count == count + 1


def test_concurrent_worker_and_mutations_are_excluded(tmp_path):
    w = worker(tmp_path)
    w.store.enqueue(DAY, ["0"], **ACTOR)
    with w.store.lock():
        assert worker(tmp_path).tick(NOW) == "busy"
        with pytest.raises(cloud.Timeout):
            w.store.enqueue(DAY, ["1"], **ACTOR)
    assert w.store.history()[0]["attempts"] == 0


def test_automatic_schedule_uses_local_time_and_does_not_duplicate(tmp_path):
    config = Config()
    config.value.update(enabled=True, daily_time="07:00", send_enabled=False)
    downloader = Mock()
    downloader.fetch.side_effect = lambda day, centre, config: report()
    w = worker(tmp_path, config, downloader)
    w.tick(datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc))
    assert not w.store.history()
    w.tick(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc))
    assert len(w.store.history()) == 3
    assert all(row["day"] == "2026-10-31" for row in w.store.history())
    w.tick(NOW)
    assert downloader.fetch.call_count == 3


def test_config_encrypted_admin_only_and_unverified_automation_cannot_be_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("KERING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config_store = cloud.GioConfigStore(tmp_path)
    config = config_store.load()
    config.update(username="SYNTHETIC_USER", password="SYNTHETIC_PASSWORD")
    config_store.save_for_user(config, role="admin")
    assert b"SYNTHETIC" not in (tmp_path / "config.enc").read_bytes()
    assert config_store.load()["password"] == "SYNTHETIC_PASSWORD"
    with pytest.raises(PermissionError):
        config_store.save_for_user(config, role="masterdata")
    config["enabled"] = True
    with pytest.raises(ValueError, match="bloqueada"):
        config_store.save(config)
    config["enabled"] = False
    config["daily_time"] = "25:00"
    with pytest.raises(ValueError, match="hora"):
        config_store.save(config)


def test_download_and_control_permissions(tmp_path):
    w = worker(tmp_path)
    w.store.enqueue(DAY, ["0"], originals={"0": report()}, **ACTOR)
    digest = w.store.history()[0]["original"]
    with pytest.raises(PermissionError):
        w.store.download(digest, role="masterdata", actor="synthetic")
    with pytest.raises(PermissionError):
        w.store.enqueue(DAY, ["1"], role="masterdata", actor="synthetic")
    with pytest.raises(PermissionError):
        w.store.retry(DAY.isoformat(), "0", role="masterdata", actor="synthetic")
    with pytest.raises(PermissionError):
        w.store.request_send(DAY.isoformat(), role="masterdata", actor="synthetic")
    with pytest.raises(ValueError):
        w.store.blob("..\\config.enc")


def test_existing_graph_mail_service_accepts_attachments_without_retry(monkeypatch, tmp_path):
    import graph_mail_downloader as graph

    config = graph.M365Config("tenant", "client", "secret", "configured@example.test", tmp_path)
    monkeypatch.setattr(graph, "_get_access_token", lambda _: "synthetic")
    response = Mock()
    response.raise_for_status.side_effect = requests.Timeout()
    post = Mock(return_value=response)
    monkeypatch.setattr(graph.requests, "post", post)
    with pytest.raises(requests.Timeout):
        graph.send_alert_email("subject", "body", ["to@example.test"], config,
                               attachments=[("original.csv", b"exact original")])
    assert post.call_count == 1 and response.close.called
    payload = post.call_args.kwargs["json"]
    assert post.call_args.args[0].endswith("/configured@example.test/sendMail")
    assert base64.b64decode(payload["message"]["attachments"][0]["contentBytes"]) == b"exact original"


def test_gio_ui_smoke_and_no_background_processing_in_request(tmp_path, monkeypatch):
    monkeypatch.setenv("GIO_DATA_ROOT", str(tmp_path))
    app = AppTest.from_string("""
import streamlit as st
st.session_state["auth_role"] = "admin"
st.session_state["auth_user"] = "synthetic"
from app_pages.gio import render
render()
""").run()
    assert not app.exception
    app.button[-2].click().run()  # enqueue form, before the refresh button
    assert not app.exception
    assert len(cloud.GioStore(tmp_path).history()) == 3
    assert all(row["state"] == "pending" for row in cloud.GioStore(tmp_path).history())
    denied = AppTest.from_string("""
import streamlit as st
st.session_state["auth_role"] = "masterdata"
from app_pages.gio import render
render()
""").run()
    assert denied.error and not denied.exception


def test_ui_reads_only_explicitly_prepared_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GIO_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("KERING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    cloud.GioConfigStore(tmp_path).save(settings())
    w = worker(tmp_path)
    w.store.enqueue(DAY, ["0"], originals={"0": report(movement())}, **ACTOR)
    w.tick(NOW)
    download = Mock(return_value=b"synthetic file bytes")
    monkeypatch.setattr(cloud.GioStore, "download", download)
    app = AppTest.from_string("""
import streamlit as st
st.session_state["auth_role"] = "admin"
st.session_state["auth_user"] = "synthetic"
from app_pages.gio import render
render()
""").run()
    assert not app.exception and not download.called
    app.button(key="gio_prepare_download").click().run()
    assert not app.exception and download.call_count == 1 and app.get("download_button")
    app.run()
    assert download.call_count == 1


def test_navigation_only_exposes_gio_to_admin():
    from app_navigation import build_navigation
    from test_app_navigation import FakeStreamlit, _pages

    for role in ("admin", "masterdata", None):
        st = FakeStreamlit()
        build_navigation(st, role, {**_pages(), "gio": "gio"})
        assert ("Contabilidad" in st.groups) == (role == "admin")


def test_provider_worker_drains_gio_even_if_kering_fails(monkeypatch, tmp_path):
    import kering_jobs

    monkeypatch.setenv("KERING_DATA_ROOT", str(tmp_path / "kering"))
    monkeypatch.setenv("IMAGE_REPOSITORY_ROOT", str(tmp_path / "repository"))
    monkeypatch.setenv("GIO_DATA_ROOT", str(tmp_path / "gio"))
    gio_tick = Mock()
    monkeypatch.setattr(cloud, "tick", gio_tick)
    monkeypatch.setattr(kering_jobs.KeringScheduler, "tick", Mock(side_effect=RuntimeError()))
    assert kering_jobs.main(["--once"]) == 0
    gio_tick.assert_called_once()
