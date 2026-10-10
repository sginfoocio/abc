#!/bin/sh
# Only dedicated staging exports; never restart the shared production mount.
set -eu
test "$(id -u)" = 0 || { echo "Run with sudo"; exit 1; }
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -f "$TMP"/*.mount; rmdir "$TMP"' EXIT

# Validate every destination before installing or starting any unit.
while read -r SHARE ROOT MINIMUM; do
    case "$SHARE" in ''|\#*) continue ;; esac
    SOURCE="192.168.1.32:/volume1/$SHARE"
    UNIT="$(systemd-escape --path --suffix=mount "$ROOT")"
    test ! -L "$ROOT" || { echo "Symlink forbidden: $ROOT"; exit 1; }
    test ! -L "/etc/systemd/system/$UNIT" || { echo "Unit symlink requires review: $UNIT"; exit 1; }
    if grep -Eq "^[^#]+[[:space:]]$ROOT[[:space:]]" /etc/fstab; then
        echo "fstab already manages $ROOT; review before adding a unit"; exit 1
    fi
    if mountpoint -q "$ROOT"; then
        python3 "$HERE/nfs_repository_guard.py" --root "$ROOT" --source "$SOURCE"
    elif test -e "$ROOT"; then
        test -d "$ROOT" && test -z "$(find "$ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ||
            { echo "Nonempty or invalid underlying directory: $ROOT"; exit 1; }
        test "$(stat -c '%u:%g:%a' "$ROOT")" = "0:0:0" ||
            { echo "Existing local directory requires root-only mode000 review: $ROOT"; exit 1; }
    fi
    sed -e "s|192.168.1.32:/volume1/cloud-imagenes|$SOURCE|" \
        -e "s|/mnt/cloud-imagenes|$ROOT|" \
        "$HERE/cloud-images.mount.template" > "$TMP/$UNIT"
    FRAGMENT="$(systemctl show "$UNIT" -p FragmentPath --value)"
    if test -n "$FRAGMENT"; then
        cmp -s "$TMP/$UNIT" "$FRAGMENT" ||
            { echo "Different persistent unit requires review: $UNIT"; exit 1; }
    fi
    systemd-analyze verify "$TMP/$UNIT"
done < "$HERE/staging-exports.tsv"

while read -r SHARE ROOT MINIMUM; do
    case "$SHARE" in ''|\#*) continue ;; esac
    UNIT="$(systemd-escape --path --suffix=mount "$ROOT")"
    if test ! -e "$ROOT"; then
        # Underlying local fallback is inaccessible to image services.
        install -d -o 0 -g 0 -m 000 "$ROOT"
    fi
    install -m 0644 "$TMP/$UNIT" "/etc/systemd/system/$UNIT"
done < "$HERE/staging-exports.tsv"
systemctl daemon-reload
while read -r SHARE ROOT MINIMUM; do
    case "$SHARE" in ''|\#*) continue ;; esac
    UNIT="$(systemd-escape --path --suffix=mount "$ROOT")"
    systemctl enable "$UNIT"
    # Start only a new, unmounted staging destination. Never remount/restart.
    if ! mountpoint -q "$ROOT"; then systemctl start "$UNIT"; fi
    python3 "$HERE/nfs_repository_guard.py" --root "$ROOT" \
        --source "192.168.1.32:/volume1/$SHARE"
done < "$HERE/staging-exports.tsv"
echo "Dedicated mounts installed; capacity and UID permissions still require verification"
