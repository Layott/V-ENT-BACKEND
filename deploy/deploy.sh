#!/usr/bin/env bash
# Deploy main onto the VPS. Run as the `vent` user.
set -euo pipefail

echo "--- backend ---"
cd /srv/vent/backend
git pull --ff-only
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput
# Reload, not restart: the master keeps the socket bound and re-execs its
# workers, so nothing in flight is dropped. Falls back to a restart if the
# reload fails, and a restart is still what you want after changing the unit
# file or .env, neither of which a reload reads.
sudo systemctl reload vent-api || sudo systemctl restart vent-api

echo "--- frontend ---"
cd /srv/vent/frontend
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

cp -rn "$CARRY"/. .next/static/ || true          # old names back, new ones win
cp -r  .next/static/. "$CARRY"/                  # and this build joins the carry
find "$CARRY" -type f -mtime +7 -delete || true
find "$CARRY" -type d -empty -delete || true

# `-T`: the destination IS the directory. Without it, `cp -r a b` puts `a`
# inside `b` once `b` exists, so every deploy after the first buried the new
# files one level deeper while the originals sat there looking correct.
mkdir -p .next/standalone/.next/static .next/standalone/public
cp -rT .next/static  .next/standalone/.next/static
cp -rT public        .next/standalone/public

# The written page nginx shows while this is happening.
sudo install -m 0644 /srv/vent/backend/deploy/maintenance.html /srv/vent/maintenance.html

sudo systemctl restart vent-web

systemctl is-active vent-api vent-web
