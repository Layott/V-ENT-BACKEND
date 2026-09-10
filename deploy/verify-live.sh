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

# The build id out of a health answer.
#
# The App Router does NOT put the build id in the HTML: every asset is
# /_next/static/chunks/... with no id in the path, so reading it off the page
# only works for the pages router. The first version of this check did exactly
# that, found nothing on a healthy site, and failed. That is the right way
# round for a check to be wrong, and it is why the health endpoint publishes
# the id instead.
read_build() {
    sed -n 's/.*"build":"\([^"]*\)".*/\1/p'
}

health_build() {
    local port=$1
    curl -sf --max-time 20 "http://127.0.0.1:$port/api/health" | read_build
}

# The pages-router fallback, for anything still served that way. Kept because
# it costs nothing and it is the only signal left if the health route is ever
# the thing that broke.
manifest_build() {
    grep -o '/_next/static/[A-Za-z0-9_-]\{6,\}/_buildManifest' | head -1 | cut -d/ -f4
}

served_build() {
    local port=$1
    curl -sf --max-time 20 "http://127.0.0.1:$port/" | manifest_build
}

# The id this instance is really on, whichever way it can be read.
build_on() {
    local port=$1 id
    id=$(health_build "$port")
    if [ -z "$id" ] || [ "$id" = "unknown" ]; then
        id=$(served_build "$port")
    fi
    printf '%s' "$id"
}

self_test() {
    local want='-VvxB_POy5Ptwp3KlLveO'

    # A real build id from this site, and it begins with a hyphen. Nothing here
    # may treat one as an option, and the comparison is a plain string compare.
    local ok='{"ok":true,"port":"3000","build":"-VvxB_POy5Ptwp3KlLveO","uptime":114}'
    local stale='{"ok":true,"port":"3000","build":"OLDBUILDXYZ","uptime":90000}'
    local unknown='{"ok":true,"port":"3000","build":"unknown","uptime":90000}'

    local got
    got=$(printf '%s' "$ok" | read_build)
    [ "$got" = "$want" ] || { echo "self-test: the matching build was not recognised ($got)"; exit 1; }

    got=$(printf '%s' "$stale" | read_build)
    [ "$got" != "$want" ] || { echo "self-test: a stale build was accepted"; exit 1; }
    [ "$got" = "OLDBUILDXYZ" ] || { echo "self-test: read the wrong id off a stale instance ($got)"; exit 1; }

    got=$(printf '%s' "$unknown" | read_build)
    [ "$got" = "unknown" ] || { echo "self-test: unknown was not read as unknown ($got)"; exit 1; }

    # An instance that answers nothing must not read as a match: that is the
    # shape the real fault took, and an empty string equals an empty string.
    got=$(printf '%s' '' | read_build)
    [ -z "$got" ] || { echo "self-test: an empty answer produced an id"; exit 1; }

    # The pages-router fallback, both ways.
    got=$(printf '%s' '<script src="/_next/static/abc123/_buildManifest.js">' | manifest_build)
    [ "$got" = "abc123" ] || { echo "self-test: the fallback did not read a pages-router id ($got)"; exit 1; }

    got=$(printf '%s' '<script src="/_next/static/chunks/main-app.js">' | manifest_build)
    [ -z "$got" ] || { echo "self-test: the fallback invented an id from App Router HTML ($got)"; exit 1; }

    echo "self-test: 7 cases, all as expected"
}

if [ "${1:-}" = "--self-test" ]; then
    self_test
    exit 0
fi

WANT=$(cat "$FRONTEND/.next/BUILD_ID" 2>/dev/null || true)
[ -n "$WANT" ] || fail "no $FRONTEND/.next/BUILD_ID, so there is nothing to compare against"

for PORT in "${PORTS[@]}"; do
    GOT=$(build_on "$PORT")
    [ -n "$GOT" ] || fail "instance on $PORT answered nothing, so it is not serving this build"
    [ "$GOT" != "unknown" ] || fail "instance on $PORT will not say which build it is on, so this cannot be verified"
    if [ "$GOT" != "$WANT" ]; then
        fail "instance on $PORT is serving build $GOT, the build on disk is $WANT. The roll did not happen. Fix: sudo systemctl restart vent-web@$PORT"
    fi
    echo "port $PORT is serving $GOT"
done

# The backend half. A migration that did not run is the other way a deploy
# looks finished and is not.
if [ -x "$BACKEND/venv/bin/python" ]; then
    UNAPPLIED=$("$BACKEND/venv/bin/python" "$BACKEND/manage.py" showmigrations --plan 2>/dev/null | grep -c '^\[ \]' || true)
    [ "${UNAPPLIED:-0}" = "0" ] || fail "$UNAPPLIED migration(s) have not been applied"
    echo "migrations: none outstanding"
fi

echo "the live site is serving this build"
