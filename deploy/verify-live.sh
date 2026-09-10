#!/usr/bin/env bash
# Did the deploy actually land? Ask the running site, not systemd.
#
# 10 September 2026. A deploy pulled both repos, ran the migrations, built the
# frontend and printed `active` for every unit, and the site went on serving
# the PREVIOUS frontend build for twenty minutes. Nothing in the output was
# wrong; the instances were simply never restarted, because the documented
# path `/srv/vent/deploy/deploy.sh` was a copy from 1 September that still
# restarted the retired single-instance unit.
#
# `systemctl is-active` cannot see that. It answers "is a process running",
# and one was: yesterday's. The only honest question is what the thing on the
# port is serving RIGHT NOW, which is what this asks.
#
# This is the second time a deploy script has exited 0 having done nothing:
# the backup cron did it on 9 September, failing with Permission denied every
# night while its own log said the run had started. Twice is a class, and a
# class gets a check that fails.
#
#   deploy/verify-live.sh              check the live instances
#   deploy/verify-live.sh --self-test  prove the check can fail
set -uo pipefail

FRONTEND=${FRONTEND:-/srv/vent/frontend}
BACKEND=${BACKEND:-/srv/vent/backend}
PORTS=(${PORTS:-3000 3001})

fail() { echo "VERIFY FAILED: $*" >&2; exit 1; }

# The build id a browser is being served, taken from the HTML rather than from
# a header: every page references /_next/static/<BUILD_ID>/_buildManifest.js,
# so this is the same string the browser resolves its chunks against.
served_build() {
    local port=$1
    curl -sf --max-time 20 "http://127.0.0.1:$port/" \
        | grep -o '/_next/static/[A-Za-z0-9_-]\{6,\}/_buildManifest' \
        | head -1 | cut -d/ -f4
}

# What the process says about itself. Useful when the page cannot be fetched,
# and it is the field the health endpoint exists to publish.
health_build() {
    local port=$1
    curl -sf --max-time 20 "http://127.0.0.1:$port/api/health" \
        | sed -n 's/.*"build":"\([^"]*\)".*/\1/p'
}

self_test() {
    local tmp; tmp=$(mktemp -d)
    printf 'abc123\n' > "$tmp/BUILD_ID"
    local want; want=$(cat "$tmp/BUILD_ID")

    local html_match='<script src="/_next/static/abc123/_buildManifest.js"></script>'
    local html_stale='<script src="/_next/static/OLDBUILDXYZ/_buildManifest.js"></script>'

    local got
    got=$(printf '%s' "$html_match" | grep -o '/_next/static/[A-Za-z0-9_-]\{6,\}/_buildManifest' | head -1 | cut -d/ -f4)
    [ "$got" = "$want" ] || { echo "self-test: matching build was not recognised ($got)"; exit 1; }

    got=$(printf '%s' "$html_stale" | grep -o '/_next/static/[A-Za-z0-9_-]\{6,\}/_buildManifest' | head -1 | cut -d/ -f4)
    [ "$got" != "$want" ] || { echo "self-test: a stale build was accepted"; exit 1; }
    [ "$got" = "OLDBUILDXYZ" ] || { echo "self-test: read the wrong id off the stale page ($got)"; exit 1; }

    # And the shape the real fault took: a page that cannot be fetched at all
    # must not read as a match, because an empty string equals an empty string.
    got=$(printf '%s' '' | grep -o '/_next/static/[A-Za-z0-9_-]\{6,\}/_buildManifest' | head -1 | cut -d/ -f4)
    [ -z "$got" ] || { echo "self-test: empty page produced an id"; exit 1; }

    rm -rf "$tmp"
    echo "self-test: 4 cases, all as expected"
}

if [ "${1:-}" = "--self-test" ]; then
    self_test
    exit 0
fi

WANT=$(cat "$FRONTEND/.next/BUILD_ID" 2>/dev/null || true)
[ -n "$WANT" ] || fail "no $FRONTEND/.next/BUILD_ID, so there is nothing to compare against"

for PORT in "${PORTS[@]}"; do
    GOT=$(served_build "$PORT")
    [ -n "$GOT" ] || fail "instance on $PORT served no page, so it is not serving this build"
    if [ "$GOT" != "$WANT" ]; then
        fail "instance on $PORT is serving build $GOT, the build on disk is $WANT. The roll did not happen. Fix: sudo systemctl restart vent-web@$PORT"
    fi
    SAYS=$(health_build "$PORT")
    echo "port $PORT serving $GOT (health says ${SAYS:-nothing})"
done

# The backend half. A migration that did not run is the other way a deploy
# looks finished and is not.
if [ -x "$BACKEND/venv/bin/python" ]; then
    UNAPPLIED=$("$BACKEND/venv/bin/python" "$BACKEND/manage.py" showmigrations --plan 2>/dev/null | grep -c '^\[ \]' || true)
    [ "${UNAPPLIED:-0}" = "0" ] || fail "$UNAPPLIED migration(s) have not been applied"
    echo "migrations: none outstanding"
fi

echo "the live site is serving this build"
