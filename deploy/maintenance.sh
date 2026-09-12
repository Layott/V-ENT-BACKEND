#!/usr/bin/env bash
# Put the maintenance page up, or take it down, on purpose.
#
#     deploy/maintenance.sh up
#     deploy/maintenance.sh down
#     deploy/maintenance.sh status
#
# An ordinary deploy no longer needs this: two Next instances are rolled one at
# a time and nobody sees anything. But some changes genuinely warrant it - a
# destructive migration, a data repair, restoring a backup - and the honest
# thing is to keep the option and CHOOSE it, rather than to have deleted it
# because the normal case stopped needing it.
set -euo pipefail

FLAG=/srv/vent/maintenance.on
PAGE=/srv/vent/maintenance.html
SRC="$(cd "$(dirname "$0")" && pwd)/maintenance.html"

case "${1:-status}" in
  up)
    sudo install -m 0644 "$SRC" "$PAGE"
    sudo touch "$FLAG"
    echo "maintenance page UP. Take it down with: $0 down"
    ;;
  down)
    sudo rm -f "$FLAG"
    echo "maintenance page down"
    ;;
  status)
    if [ -f "$FLAG" ]; then
      echo "UP - the site is showing the maintenance page"
      exit 1
    fi
    echo "down - the site is serving normally"
    ;;
  *)
    echo "usage: $0 up|down|status" >&2
    exit 2
    ;;
esac
