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
pnpm build
cp -r .next/static  .next/standalone/.next/static
cp -r public        .next/standalone/public
sudo systemctl restart vent-web

systemctl is-active vent-api vent-web
