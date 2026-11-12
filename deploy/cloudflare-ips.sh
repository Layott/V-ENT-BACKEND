#!/usr/bin/env bash
# The visitor's real address when Cloudflare's proxy is on (inbox 597, 12 November 2026).
#
# With the orange cloud on, every connection to this box comes from one of
# Cloudflare's edges, and the person behind it is named in CF-Connecting-IP.
# nginx's realip module puts that address back into $remote_addr, but ONLY for a
# connection that really comes from Cloudflare: anybody else sending the header
# straight to the box is ignored, so it cannot be used to pretend to be the box
# or anybody else. That is why the trusted list must be Cloudflare's own, and
# why this script refuses a list that looks wrong rather than trusting it.
#
#   deploy/cloudflare-ips.sh --print            write the file to stdout (to refresh deploy/nginx-cloudflare.conf)
#   sudo deploy/cloudflare-ips.sh --install     fetch, write /etc/nginx/conf.d/vent-cloudflare.conf,
#                                               nginx -t, reload; the old file comes back if the test fails
#
# Cloudflare publishes the lists at https://www.cloudflare.com/ips-v4 and ips-v6
# and changes them rarely. Run --install after a change is announced, or monthly
# from cron: `0 4 1 * * /srv/vent/backend/deploy/cloudflare-ips.sh --install`.
set -euo pipefail

TARGET=/etc/nginx/conf.d/vent-cloudflare.conf
V4_RE='^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$'
V6_RE='^[0-9a-f:]+/[0-9]{1,3}$'

fetch() {
  curl -fsS --max-time 20 "$1" | tr -d '\r' | sed '/^[[:space:]]*$/d'
}

render() {
  local v4 v6
  v4=$(fetch https://www.cloudflare.com/ips-v4)
  v6=$(fetch https://www.cloudflare.com/ips-v6)
  # A short or malformed list is refused: trusting a wrong one would let a
  # stranger choose their own address.
  local n4 n6
  n4=$(printf '%s\n' "$v4" | grep -cE "$V4_RE" || true)
  n6=$(printf '%s\n' "$v6" | grep -ciE "$V6_RE" || true)
  if [ "$n4" -lt 10 ] || [ "$n6" -lt 3 ] \
     || [ "$n4" -ne "$(printf '%s\n' "$v4" | wc -l)" ] \
     || [ "$n6" -ne "$(printf '%s\n' "$v6" | wc -l)" ]; then
    echo "cloudflare-ips: the published lists look wrong (v4 $n4, v6 $n6); nothing changed" >&2
    exit 1
  fi
  echo "# Cloudflare's edges, from https://www.cloudflare.com/ips-v4 and ips-v6 ($(date -u +%Y-%m-%d))."
  echo "# Written by deploy/cloudflare-ips.sh; do not edit by hand. Lives at $TARGET."
  echo "# Inside http {}: a connection from one of these ranges has its address"
  echo "# replaced by CF-Connecting-IP; a connection from anywhere else keeps its own."
  printf '%s\n' "$v4" | sed 's/^/set_real_ip_from /; s/$/;/'
  printf '%s\n' "$v6" | sed 's/^/set_real_ip_from /; s/$/;/'
  echo "real_ip_header CF-Connecting-IP;"
}

case "${1:-}" in
  --print)
    render
    ;;
  --install)
    new=$(mktemp)
    render > "$new"
    if [ -f "$TARGET" ] && cmp -s "$new" "$TARGET"; then
      echo "cloudflare-ips: unchanged"; rm -f "$new"; exit 0
    fi
    backup=""
    if [ -f "$TARGET" ]; then backup=$(mktemp); cp "$TARGET" "$backup"; fi
    install -m 0644 "$new" "$TARGET"; rm -f "$new"
    if nginx -t -q; then
      systemctl reload nginx
      echo "cloudflare-ips: installed $(grep -c set_real_ip_from "$TARGET") ranges, nginx reloaded"
    else
      if [ -n "$backup" ]; then cp "$backup" "$TARGET"; else rm -f "$TARGET"; fi
      echo "cloudflare-ips: nginx -t failed, previous file restored, not reloaded" >&2
      exit 1
    fi
    if [ -n "$backup" ]; then rm -f "$backup"; fi
    ;;
  *)
    echo "usage: $0 --print | --install" >&2
    exit 2
    ;;
esac
