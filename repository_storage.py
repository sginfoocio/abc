"""Image bytes and durable local state have independent, validated storage roots."""
import os
from pathlib import Path

from scripts.nfs_repository_guard import require_nfs, _unescape


def filesystem_mount(path: Path):
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists():
        return None
    path = path.resolve()
    matches = []
    for line in mountinfo.read_text(encoding="utf-8").splitlines():
        before, _, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        mountpoint = Path(_unescape(fields[4]))
        if path == mountpoint or mountpoint in path.parents:
            matches.append((mountpoint, filesystem[0], _unescape(filesystem[1])))
    return max(reversed(matches), key=lambda item: len(item[0].parts)) if matches else None


def require_local(path: Path):
    mount = filesystem_mount(path)
    if mount and mount[1] in {"nfs", "nfs4", "cifs", "smb3"}:
        raise ValueError("El estado SQLite requiere almacenamiento local persistente")


def check_image_root(root: Path):
    expected = os.getenv("IMAGE_REPOSITORY_NFS_SOURCE", "").strip()
    if expected:
        require_nfs(root, expected)
    else:
        mount = filesystem_mount(root)
        if mount and mount[1] in {"nfs", "nfs4"}:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_NFS_SOURCE explicita")


def check_write_path(path: Path):
    expected = os.getenv("IMAGE_REPOSITORY_NFS_SOURCE", "").strip()
    if expected:
        configured = os.getenv("IMAGE_REPOSITORY_ROOT", "").strip()
        if not configured:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_ROOT explicita")
        root = Path(configured).resolve()
        if path.resolve() == root or root in path.resolve().parents:
            check_image_root(root)
    backup_source = os.getenv("IMAGE_REPOSITORY_BACKUP_NFS_SOURCE", "").strip()
    if backup_source:
        configured = os.getenv("IMAGE_REPOSITORY_BACKUP_ROOT", "").strip()
        if not configured:
            raise ValueError("NFS requiere IMAGE_REPOSITORY_BACKUP_ROOT explicita")
        root = Path(configured).resolve()
        if path.resolve() == root or root in path.resolve().parents:
            require_nfs(root, backup_source)
    mount = filesystem_mount(path)
    if mount and mount[1] in {"nfs", "nfs4"}:
        if mount[2] not in {expected, backup_source}:
            raise ValueError("Escritura NFS sin exportacion esperada")
        require_nfs(mount[0], mount[2])


def validate_state_root(images: Path, root: Path) -> None:
    require_local(root)
    if root != images and (root in images.parents or images in root.parents):
        raise ValueError("Imagenes y estado deben tener raices separadas, no solapadas")


def state_root(images: Path) -> Path:
    from db_config import load_env_file
    load_env_file()
    configured = os.getenv("IMAGE_REPOSITORY_STATE_ROOT", "").strip()
    root = Path(configured).resolve() if configured else images.resolve()
    validate_state_root(images.resolve(), root)
    return root


def backup_root(images: Path) -> Path:
    configured = os.getenv("IMAGE_REPOSITORY_BACKUP_ROOT", "").strip()
    return Path(configured).resolve() if configured else state_root(images)


def check_backup_root(images: Path, backups: Path):
    check_image_root(images)
    if backups == images or backups in images.parents or images in backups.parents:
        if state_root(images) != images:
            raise ValueError("Backups de imagenes requieren ubicacion independiente")
    expected = os.getenv("IMAGE_REPOSITORY_BACKUP_NFS_SOURCE", "").strip()
    if expected:
        require_nfs(backups, expected)
    mount = filesystem_mount(backups)
    if mount and mount[1] in {"nfs", "nfs4"}:
        if not expected or mount[2] != expected:
            raise ValueError("Exportacion de backups no acreditada")
        require_distinct_replica(images, backups)


def require_distinct_replica(source: Path, target: Path):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("El NAS debe ser una replica separada")
    if target.exists() and source.exists() and os.path.samefile(source, target):
        raise ValueError("Origen y replica son el mismo almacenamiento")
    left, right = filesystem_mount(source), filesystem_mount(target)
    if left and right and left[1] in {"nfs", "nfs4"} and right[1] in {"nfs", "nfs4"} and left[2] == right[2]:
        raise ValueError("No replicar sobre la misma exportacion NFS, aunque cambie el montaje")
