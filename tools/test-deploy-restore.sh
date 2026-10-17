#!/usr/bin/env bash
# Runs the build block of deploy/deploy.sh against a fake frontend whose
# "pnpm build" empties .next/standalone and .next/static and dies with 134,
# as the out-of-memory build did on 17 October 2026. Passes when the files the
# live servers read are back afterwards and the block exits non-zero.
set -u
DEPLOY="${1:-deploy/deploy.sh}"
T=$(mktemp -d)
mkdir -p "$T/fe/.next/standalone/public/images" "$T/fe/.next/standalone/.next/server" "$T/fe/.next/static/chunks" "$T/bin"
echo server > "$T/fe/.next/standalone/server.js"
echo png > "$T/fe/.next/standalone/public/images/og-default.png"
echo js > "$T/fe/.next/static/chunks/webpack-live.js"
cat > "$T/bin/pnpm" <<'EOF'
#!/usr/bin/env bash
rm -rf .next/standalone/* .next/static/*
exit 134
EOF
chmod +x "$T/bin/pnpm"

# The block from the CARRY line to the end of the build's failure branch,
# with the box's paths pointed at the fake.
BLOCK=$(sed -n '/^CARRY=\/srv\/vent\/frontend-static-carry/,/^fi$/p' "$DEPLOY" \
  | sed "s#/srv/vent/frontend-static-carry#$T/carry#; s#/srv/vent/frontend-standalone-prev#$T/prev#")
cd "$T/fe"
( set -euo pipefail; PATH="$T/bin:$PATH"; eval "$BLOCK" ) >/dev/null 2>&1; code=$?

fail=0
[ "$code" -ne 0 ] || { echo "FAIL: the block exited 0 after a failed build"; fail=1; }
[ -f .next/standalone/server.js ] || { echo "FAIL: server.js not back"; fail=1; }
[ -f .next/standalone/public/images/og-default.png ] || { echo "FAIL: public images not back"; fail=1; }
[ -f .next/static/chunks/webpack-live.js ] || { echo "FAIL: live chunk not back"; fail=1; }
rm -rf "$T"
[ "$fail" -eq 0 ] && echo "restore after a failed build: passed" || exit 1
