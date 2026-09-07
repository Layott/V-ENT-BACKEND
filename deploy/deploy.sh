#!/usr/bin/env bash
# Deploy main onto the VPS. Run as the `vent` user.
#
# CEO, 7 September 2026: "is it possible for the site to update while people are
# still using it? and they dont even know what was happening."
#
# It is, and this is it. No maintenance page.
#
# ## What made the page necessary before
#
# Two things, and only one of them was ever really unavoidable:
#
#   1. `systemctl restart vent-web` left nothing listening on 3000 for several
#      seconds. That is the one this fixes: two instances now sit behind an
#      nginx upstream and are rolled ONE AT A TIME, so nginx always has a
#      healthy one, and a request that lands on the instance being restarted is
#      retried on the other by `proxy_next_upstream`.
#
#   2. The migration window: the schema changes before the new code is live. A
#      process supervisor cannot make that invisible; only writing migrations
#      so both versions tolerate the schema can. See the note at the bottom.
#
# The API never needed the page at all. `systemctl reload vent-api` re-execs
# gunicorn's workers while the master keeps the socket bound, so no request is
# dropped, and that was already true before today.
#
# ## The maintenance page has not been deleted
#
# It is still installed, and `deploy/maintenance.sh` puts it up by hand. Some
# changes genuinely warrant it - a destructive migration, a data repair - and
# the honest thing is to have it and choose, rather than to have removed the
# option because the normal case no longer needs it.
set -euo pipefail

PORTS=(3000 3001)
FRONTEND=/srv/vent/frontend
BACKEND=/srv/vent/backend

say() { echo ""; echo "--- $* ---"; }

# Wait until an instance answers its own health endpoint. The unit's
# ExecStartPost does this too; doing it here as well means this script never
# moves to the second instance on the strength of systemd having returned.
wait_healthy() {
    local port=$1
    for _ in $(seq 1 60); do
        if curl -sf -o /dev/null "http://127.0.0.1:$port/api/health"; then
            return 0
        fi
        sleep 1
    done
    echo "instance on $port did not become healthy" >&2
    return 1
}

say "backend"
cd "$BACKEND"
git pull --ff-only
./venv/bin/pip install -r requirements.txt

# Migrations run BEFORE the new frontend, and while the old API is still
# serving. That is safe for an additive change and unsafe for a destructive
# one, which is the whole reason for the rule at the bottom of this file.
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput

# Reload, not restart: the master keeps the socket bound and re-execs its
# workers, so nothing in flight is dropped. Falls back to a restart if the
# reload fails, and a restart is still what you want after changing the unit
# file or .env, neither of which a reload reads.
sudo systemctl reload vent-api || sudo systemctl restart vent-api

say "frontend build"
cd "$FRONTEND"
git pull --ff-only
pnpm install --frozen-lockfile

# The page somebody already has open asks for the chunk names of the build it
# was served by. `pnpm build` writes new fingerprinted names and removes the old
# ones, and nginx serves that directory off disk, so the moment a deploy lands
# every open page starts 404ing its own stylesheet. It renders as raw HTML
# inside an already-painted shell, which reads as a broken site rather than a
# deploy.
#
# So the previous build's files are set aside and merged back afterwards. They
# are content-addressed: an old page asks for an old name and finds it. Anything
# untouched for a week is from a build nobody still has open.
CARRY=/srv/vent/frontend-static-carry
mkdir -p "$CARRY"
[ -d .next/static ] && cp -r .next/static/. "$CARRY"/ || true

pnpm build

cp -rn "$CARRY"/. .next/static/ 2>/dev/null || true   # old names back, new ones win
cp -r  .next/static/. "$CARRY"/                       # and this build joins the carry
find "$CARRY" -type f -mtime +7 -delete || true
find "$CARRY" -type d -empty -delete || true

# `-T`: the destination IS the directory. Without it, `cp -r a b` puts `a`
# inside `b` once `b` exists, so every deploy after the first buried the new
# files one level deeper while the originals sat there looking correct.
mkdir -p .next/standalone/.next/static .next/standalone/public
cp -rT .next/static  .next/standalone/.next/static
cp -rT public        .next/standalone/public

say "rolling the instances one at a time"
#
# THE PART THAT REPLACES THE MAINTENANCE PAGE.
#
# One instance at a time, and the next one is not touched until the previous
# has answered its own health endpoint. If an instance fails to come back, the
# script stops HERE with the other one still serving the OLD build, which is a
# site that works rather than a site that is half deployed.
for PORT in "${PORTS[@]}"; do
    echo "restarting vent-web@$PORT"
    sudo systemctl restart "vent-web@$PORT"
    wait_healthy "$PORT"
    echo "vent-web@$PORT healthy"
done

systemctl is-active vent-api "vent-web@${PORTS[0]}" "vent-web@${PORTS[1]}"

say "done, and nobody saw a page"

# ---------------------------------------------------------------------------
# The half a process supervisor cannot fix
# ---------------------------------------------------------------------------
#
# For the seconds between the migration and the last instance restarting, the
# NEW schema is live and the OLD code is still serving. That is fine for an
# additive change and fatal for a destructive one.
#
# So: EXPAND, then CONTRACT, in two separate deploys.
#
#   Deploy 1  add the column, and write code that can work with or without it.
#   Deploy 2  once nothing reads the old shape, remove it.
#
# Never rename or drop a column in the same deploy as the code that stops using
# it. If you must, put the maintenance page up on purpose with
# `deploy/maintenance.sh up`, and take it down after. Choosing it is fine.
# Needing it every time was the problem.
