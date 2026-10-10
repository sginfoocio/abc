"""Real NFS probe in a dedicated container mount/network namespace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from nfs_repository_guard import require_nfs


ROOT = Path("/isolated-nfs")
SOURCE = "192.168.1.32:/volume1/cloud-imagenes"
STATE = Path("/tmp/nfs-probe-state")


def nonroot_preexec(uid):
    def change_identity():
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    return change_identity


def setup():
    require_nfs(ROOT, SOURCE)
    temporary = tempfile.mkdtemp(prefix=".cloud-isolated-nfs-", dir=ROOT)
    directory = Path(temporary)
    os.chown(directory, 10001, 10001)
    directory.chmod(0o2770)
    STATE.mkdir()
    (STATE / "directory").write_text(temporary)
    owner = subprocess.run(["python", "-c",
        "from pathlib import Path; import sys; p=Path(sys.argv[1])/'uid10001'; p.write_bytes(b'probe'); "
        "assert p.read_bytes()==b'probe' ", temporary], preexec_fn=nonroot_preexec(10001), capture_output=True)
    outsider = subprocess.run(["python", "-c",
        "from pathlib import Path; import sys; (Path(sys.argv[1])/'forbidden').write_bytes(b'probe')",
        temporary], preexec_fn=nonroot_preexec(10002), capture_output=True)
    permissions = {"uid10001_write": owner.returncode == 0, "uid10002_denied": outsider.returncode != 0,
                   "directory_uid": directory.stat().st_uid, "directory_gid": directory.stat().st_gid,
                   "mode": oct(directory.stat().st_mode & 0o7777),
                   "uid10001_permission_error": b"PermissionError" in owner.stderr,
                   "uid10002_permission_error": b"PermissionError" in outsider.stderr}
    (STATE / "permissions.json").write_text(json.dumps(permissions))
    print(json.dumps(permissions))
    if owner.returncode != 0 or outsider.returncode == 0:
        print("Minimum-permissions activation BLOCKED; continue only root isolated transport test")


def write_during_outage():
    directory = Path((STATE / "directory").read_text())
    (STATE / "writer-started").touch()
    content = b"isolated-network-loss-probe\n" * 4096
    with (directory / "during-outage.bin").open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    assert hashlib.sha256((directory / "during-outage.bin").read_bytes()).digest() == hashlib.sha256(content).digest()
    (STATE / "writer-finished").touch()
    print("Pending hard-NFS write resumed; checksum verified")


def cleanup():
    directory = Path((STATE / "directory").read_text())
    require_nfs(ROOT, SOURCE)
    assert directory.parent == ROOT and directory.name.startswith(".cloud-isolated-nfs-")
    for path in directory.iterdir():
        assert path.is_file()
        path.unlink()
    directory.rmdir()
    print("Only isolated probe files removed")


def locks_after_reconnect():
    require_nfs(ROOT, SOURCE)
    directory = Path((STATE / "directory").read_text())
    code = """
import fcntl, os, sys, time
from pathlib import Path
directory, index = Path(sys.argv[1]), sys.argv[2]
with (directory / 'shared.lock').open('a+b') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with (directory / 'sequence.txt').open('a') as stream:
        stream.write('start-' + index + '\\n'); stream.flush(); os.fsync(stream.fileno())
        time.sleep(.2)
        stream.write('end-' + index + '\\n'); stream.flush(); os.fsync(stream.fileno())
"""
    workers = [subprocess.Popen(["python", "-c", code, str(directory), str(index)]) for index in range(2)]
    for worker in workers:
        if worker.wait(timeout=30) != 0:
            raise RuntimeError("Post-reconnection lock worker failed")
    assert (directory / "sequence.txt").read_text().splitlines() in [
        ["start-0", "end-0", "start-1", "end-1"], ["start-1", "end-1", "start-0", "end-0"]]
    print("Two NFS lock writers serialized after reconnection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "write", "locks", "cleanup"])
    action = parser.parse_args().action
    {"setup": setup, "write": write_during_outage, "locks": locks_after_reconnect, "cleanup": cleanup}[action]()
