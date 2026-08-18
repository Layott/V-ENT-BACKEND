#!/usr/bin/env bash
# Nightly database + media backup. Cron: 0 3 * * * /srv/vent/deploy/backup.sh
# Pull these off the box regularly - a backup on the same disk is not a backup.
set -euo pipefail

STAMP=$(date +%F-%H%M)
DEST=/srv/vent/backups
mkdir -p "$DEST"
cd "$DEST"

mysqldump --single-transaction --routines --triggers vent | gzip > "db-$STAMP.sql.gz"
tar czf "media-$STAMP.tar.gz" -C /srv/vent media private

find "$DEST" -name '*.gz' -mtime +14 -delete
echo "$(date -Is) backup ok: db-$STAMP.sql.gz"
