from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_staging_exports as staging


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deployment/nfs/staging-exports.tsv"


def test_canonical_five_destinations_are_distinct_and_exclude_production():
    rows = staging.read_exports(MANIFEST)
    assert len(rows) == 5
    assert sum(minimum for _, _, minimum in rows) == 136 * 1024**3
    assert {root.name for _, root, _ in rows} == {
        "cloud-migration-source", "cloud-imagenes-staging", "cloud-migration-backups",
        "cloud-migration-recovery", "cloud-image-work",
    }


@pytest.mark.parametrize("replacement", [
    "cloud-imagenes /mnt/cloud-imagenes 10",
    "cloud-image-work /mnt/other 10",
    "cloud-image-work /mnt/cloud-image-work 0",
])
def test_manifest_rejects_production_mismatch_or_invalid_capacity(tmp_path, replacement):
    manifest = tmp_path / "exports"
    lines = MANIFEST.read_text().splitlines()
    lines[-1] = replacement
    manifest.write_text("\n".join(lines))
    with pytest.raises(ValueError):
        staging.read_exports(manifest)


@pytest.fixture
def preflight(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o700)
    monkeypatch.setattr(staging.os, "getuid", lambda: 1037, raising=False)
    monkeypatch.setattr(staging.os, "getgid", lambda: 100, raising=False)
    monkeypatch.setattr(staging.os, "getgroups", lambda: [100], raising=False)
    monkeypatch.setattr(staging.os, "access", lambda *args: True)
    monkeypatch.setattr(staging.os, "statvfs",
                        lambda path: SimpleNamespace(f_bavail=1000, f_frsize=1024**3), raising=False)
    monkeypatch.setattr(staging, "require_local", lambda path: None)
    monkeypatch.setattr(staging, "require_nfs", lambda root, source: None)
    original = Path.stat

    def stat(path, *args, **kwargs):
        if path == state:
            return SimpleNamespace(st_uid=1037, st_gid=100, st_mode=0o40700)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    return state


def test_preflight_reports_only_read_only_checks(preflight):
    result = staging.verify_exports(MANIFEST, preflight)
    assert len(result["exports"]) == 5
    assert result["read_only_preflight"]
    assert result["writes_locks_and_pool_quota_not_verified"]
    assert not list(preflight.iterdir())


def test_missing_mount_stops_before_state_changes(preflight, monkeypatch):
    def missing(root, source):
        raise RuntimeError("missing")
    monkeypatch.setattr(staging, "require_nfs", missing)
    with pytest.raises(RuntimeError, match="missing"):
        staging.verify_exports(MANIFEST, preflight)
    assert not list(preflight.iterdir())


def test_preflight_checks_exact_capacity_threshold(preflight, monkeypatch):
    minimum = 64 * 1024**3
    monkeypatch.setattr(staging.os, "statvfs",
                        lambda path: SimpleNamespace(f_bavail=minimum - 1, f_frsize=1))
    with pytest.raises(RuntimeError, match="Insufficient capacity"):
        staging.verify_exports(MANIFEST, preflight)
    monkeypatch.setattr(staging.os, "statvfs",
                        lambda path: SimpleNamespace(f_bavail=minimum, f_frsize=1))
    assert staging.verify_exports(MANIFEST, preflight)["read_only_preflight"]


def test_unexpected_supplementary_group_is_blocked(preflight, monkeypatch):
    monkeypatch.setattr(staging.os, "getgroups", lambda: [100, 101])
    with pytest.raises(RuntimeError, match="supplementary"):
        staging.verify_exports(MANIFEST, preflight)


def test_isolated_probe_uses_selected_export_for_every_mount_guard():
    script = (ROOT / "deployment/nfs/validate-isolated-nfs.sh").read_text()
    assert 'SOURCE="${2:-192.168.1.32:/volume1/cloud-imagenes}"' in script
    assert '-e NFS_PROBE_SOURCE="$SOURCE"' in script
    assert script.count('--root /isolated-nfs --source "$SOURCE"') == 4
    assert "--network host" not in script and "--pid host" not in script


def test_mount_installer_is_limited_to_manifest_and_preserves_existing_mounts():
    script = (ROOT / "deployment/nfs/install-staging-mounts.sh").read_text()
    assert 'done < "$HERE/staging-exports.tsv"' in script
    assert "systemctl restart" not in script and "umount " not in script
    assert 'if ! mountpoint -q "$ROOT"; then systemctl start "$UNIT"; fi' in script
    assert 'install -d -o 0 -g 0 -m 000 "$ROOT"' in script
    assert "chmod -R" not in script and "chown -R" not in script
