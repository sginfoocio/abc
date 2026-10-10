#!/bin/sh
set -eu
if test "$(id -u)" = 1037; then
    test "$(id -g)" = 100 && test "$(id -G)" = 100 || {
        echo "Cloud image identity requires 1037:100 without supplementary groups" >&2
        exit 1
    }
    umask 077
fi
exec "$@"
