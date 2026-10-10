from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_only_image_writers_use_cloud_identity_in_preparation_override():
    config = yaml.safe_load((ROOT / "deployment/nfs/image-services.identity.compose.yml").read_text())
    assert set(config["services"]) == {"abcd-app", "abcd-luxoptica-monitor", "abcd-kering-scheduler"}
    for service in config["services"].values():
        assert service["user"] == "1037:100"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["environment"]["HOME"] == "/home/cloud-images"
    production = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name in config["services"]:
        assert "user" not in production["services"][name]


def test_staging_identity_and_owner_only_permission_proposal():
    config = yaml.safe_load((ROOT / "deployment/nfs/staging.compose.yml").read_text())
    assert config["services"]["image-storage-staging"]["user"] == "1037:100"
    script = (ROOT / "deployment/nfs/set-image-permissions.sh").read_text()
    assert "chown 1037:100" in script
    assert "chmod 0700" in script
    assert "chmod 2770" not in script
    assert "chmod -R" not in script and "chown -R" not in script
    assert "CLOUD_NAS_ACL_VERIFIED" in script


def test_nonroot_smoke_is_executed_as_confirmed_nas_identity():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "--user 1037:100" in workflow
    assert "--user 10001:10001" not in workflow
    entrypoint = (ROOT / "scripts/cloud_image_entrypoint.sh").read_text()
    assert "umask 077" in entrypoint and 'test "$(id -G)" = 100' in entrypoint
