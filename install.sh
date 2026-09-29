#!/bin/sh
set -eu
kit=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9+ is required.' >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo 'Git is required.' >&2; exit 1; }
exec python3 "$kit/scripts/manage.py" install "$@"
