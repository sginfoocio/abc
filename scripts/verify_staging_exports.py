"""Read-only host preflight for the five dedicated staging exports."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath

from scripts.nfs_repository_guard import require_nfs
from repository_storage import require_local


def read_exports(manifest: Path) -> list[tuple[str, Path, int]]:
    exports = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        share, mount, minimum = line.split()
        if not share.startswith("cloud-") or "/" in share or ".." in share:
            raise ValueError("Invalid staging share")
        root = PurePosixPath(mount)
        if not root.is_absolute() or root.parent != PurePosixPath("/mnt") or root.name != share:
            raise ValueError("Invalid staging mount")
        capacity = int(minimum)
        if capacity <= 0:
            raise ValueError("Invalid capacity")
        exports.append((f"192.168.1.32:/volume1/{share}", Path(mount), capacity))
    if len(exports) != 5 or len({row[0] for row in exports}) != 5:
        raise ValueError("Five distinct staging exports required")
    if any(source.endswith("/cloud-imagenes") for source, _, _ in exports):
        raise ValueError("Production export forbidden")
    return exports


def verify_exports(manifest: Path, state: Path) -> dict:
    if os.getuid() != 1037 or os.getgid() != 100 or set(os.getgroups()) - {100}:
        raise RuntimeError("Run as 1037:100 without supplementary groups")
    reports = []
    for source, root, minimum in read_exports(manifest):
        require_nfs(root, source)
        volume = os.statvfs(root)
        available = volume.f_bavail * volume.f_frsize
        if available < minimum:
            raise RuntimeError(f"Insufficient capacity: {root}, {available} < {minimum}")
        if not all(os.access(root, mode) for mode in (os.R_OK, os.W_OK, os.X_OK)):
            raise RuntimeError(f"Missing cloud rw/traverse: {root}")
        reports.append({"source": source, "root": str(root), "available": available,
                        "required": minimum})
    if not state.is_dir() or state.resolve() != state:
        raise RuntimeError("New local state missing or symlinked")
    require_local(state)
    info = state.stat()
    if (info.st_uid, info.st_gid, info.st_mode & 0o7777) != (1037, 100, 0o700):
        raise RuntimeError("New local state must be 1037:100 mode0700")
    volume = os.statvfs(state)
    available = volume.f_bavail * volume.f_frsize
    if available < 2 * 1024**3:
        raise RuntimeError("Local state reserve below 2 GiB")
    return {"exports": reports, "local_available": available, "read_only_preflight": True,
            "writes_locks_and_pool_quota_not_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path("/opt/cloud-image-staging/state"))
    args = parser.parse_args()
    try:
        print(json.dumps(verify_exports(args.manifest, args.state), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Staging blocked: {error}\n")
