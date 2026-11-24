import json
import sys
from unittest.mock import MagicMock

import pytest

from scripts import validate_vytria_reader as pilot


def config(**changes):
    return {"services": {"abcd-app": {"environment": {
        "PRESTASHOP_MYSQL_USER": "ps_user_vytria_lect", "PRESTASHOP_MYSQL_DATABASE": "ps_vytria",
        "PRESTASHOP_MYSQL_HOST": "mysql.example.invalid", "PRESTASHOP_MYSQL_PASSWORD": "fixture$resolved",
        "PRESTASHOP_MYSQL_PREFIX": "ps_", "PRESTASHOP_MYSQL_SSL_MODE": "REQUIRED",
        "PRESTASHOP_PRICE_SHOP_ID": "1", "PRESTASHOP_PRICE_COUNTRY_ID": "6",
        "PRESTASHOP_PRICE_CURRENCY_ID": "1", "PRESTASHOP_PRICE_GROUP_ID": "1",
        "PRESTASHOP_PRICE_ENABLED": "0", "PRESTASHOP_PRICE_CODE_AUDITED": "1", **changes,
    }}}}


def test_effective_compose_password_is_not_reinterpreted_or_written_back():
    original = config()
    environment = pilot.pilot_environment(original, "reviewer", "approved-test")
    assert environment["PRESTASHOP_MYSQL_PASSWORD"] == "fixture$resolved"
    assert environment["PRESTASHOP_PRICE_ENABLED"] == "1"
    assert original["services"]["abcd-app"]["environment"]["PRESTASHOP_PRICE_ENABLED"] == "0"
    receipt = json.loads(environment["PRESTASHOP_MYSQL_ACCOUNT_AUTHORIZATION"])
    assert receipt["user"] == environment["PRESTASHOP_MYSQL_USER"]
    assert receipt["database"] == environment["PRESTASHOP_MYSQL_DATABASE"]
    assert receipt["host"] == environment["PRESTASHOP_MYSQL_HOST"]
    assert "password" not in receipt


def test_native_pilot_preserves_php_guard_and_requires_explicit_confirmation():
    original = config(PRESTASHOP_PRICE_CODE_AUDITED="0")
    environment = pilot.pilot_environment(original, "reviewer", "approved-test", "native-test")
    assert environment["PRESTASHOP_PRICE_CODE_AUDITED"] == "0"
    assert environment["PRESTASHOP_PRICE_VALIDATION_MODE"] == "native_rules_confirmed"
    receipt = json.loads(environment["PRESTASHOP_PRICE_NATIVE_CONFIRMATION"])
    assert receipt["claim"] == "native_prestashop_catalog_rules"
    assert receipt["account_authorization_id"] == "approved-test"
    assert receipt["context"]["group_id"] == 1 and receipt["context"]["tax_included"] is True
    assert original["services"]["abcd-app"]["environment"]["PRESTASHOP_PRICE_ENABLED"] == "0"
    with pytest.raises(pilot.PilotError, match="CODE_AUDITED"):
        pilot.pilot_environment(original, "reviewer", "approved-test")


@pytest.mark.parametrize("changes", [
    {"PRESTASHOP_MYSQL_USER": "another"}, {"PRESTASHOP_MYSQL_SSL_MODE": "DISABLED"},
    {"PRESTASHOP_PRICE_COUNTRY_ID": "7"}, {"PRESTASHOP_PRICE_ENABLED": "1"},
    {"PRESTASHOP_PRICE_CODE_AUDITED": "0"}, {"PRESTASHOP_MYSQL_PASSWORD": ""},
])
def test_pilot_rejects_unapproved_context_or_missing_php_validation(changes):
    with pytest.raises(pilot.PilotError):
        pilot.pilot_environment(config(**changes), "reviewer", "approved-test")


def test_main_resolves_compose_privately_before_any_pilot_or_price_read(tmp_path, monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(pilot.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", [
        "validate_vytria_reader.py", "--checkout", str(tmp_path), "--commit", sha,
        "--actor", "reviewer", "--authorization-id", "approved-test",
    ])
    runner = MagicMock(side_effect=[
        sha.encode(), b"", json.dumps(config(PRESTASHOP_PRICE_CODE_AUDITED="0")).encode(),
    ])
    monkeypatch.setattr(pilot, "run", runner)
    with pytest.raises(pilot.PilotError, match="overrides/modulos PHP"):
        pilot.main()
    assert runner.call_args.args == ("Compose config", [
        "docker", "compose", "--project-directory", "/etc/cloud-production",
        "--project-name", "cloud-nas", "-f", "/etc/cloud-production/compose.private.json",
        "config", "--format", "json",
    ])
    assert runner.call_count == 3


def test_command_errors_do_not_publish_secret_output(monkeypatch):
    runner = MagicMock(return_value=MagicMock(returncode=1, stdout=b"secret", stderr=b"secret"))
    monkeypatch.setattr(pilot.subprocess, "run", runner)
    with pytest.raises(pilot.PilotError, match="exit 1") as error:
        pilot.run("Compose config", ["docker", "compose", "config"])
    assert "secret" not in str(error.value)
    assert runner.call_args.kwargs["capture_output"] is True
