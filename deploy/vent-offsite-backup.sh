#!/bin/bash
# vent-offsite-backup.sh - copy the nightly backup OFF the box, to Backblaze B2.
#
#   Cron (deploy/crontab.vent): 45 3 * * *, after backup.sh at 03:00, as the vent user.
#
# Why this exists. On 14 November 2026 InterServer deleted the V-ENT server for an
# unpaid invoice ("Unfortunately no data exist"). Every backup went with it, because
# backup.sh wrote them to /srv/vent/backups on the same disk, and security/backups.md
# had listed that as open since September. A backup on the box survives a bad
# migration; it does not survive losing the box.
#
# Set up once, on the box, as vent (the CEO creates the bucket and a key that can
# reach ONLY that bucket, see tasks/vps/REBUILD-2026-11-14.md):
#
#     sudo apt-get install -y rclone
#     rclone config                         new remote, name: offsite, type: b2, paste the key
#     rclone lsd offsite:                   must list the bucket
#     echo 'BUCKET=<bucket name>' > /srv/vent/offsite.conf
#     /srv/vent/backend/deploy/vent-offsite-backup.sh      once by hand, read the last line
#
# What goes up:
#   db/        every db-*.sql.gz in /srv/vent/backups (copy: nothing in the bucket is
#              ever deleted by this script; the bucket's lifecycle rule decides that)
#   media/     /srv/vent/media, synced, so only what changed travels
#   private/   /srv/vent/private (identity documents), synced the same way. The bucket
#              is private and B2 keeps a deleted file's old version for as long as the
#              lifecycle rule says, which is what makes sync safe here.
#
# Restore:
#   rclone copy offsite:$BUCKET/db/<file> /srv/vent/backups/
#   rclone sync offsite:$BUCKET/media/   /srv/vent/media/
#   rclone sync offsite:$BUCKET/private/ /srv/vent/private/
#
# Proved by contents, never by exit code (feedback_backup_verified_by_contents): after
# the copy the newest dump's size in the bucket must equal its size here, and the
# bucket must hold at least as many media and private files as the box. Anything else
# fails, says why, and mails BACKUP_ALERT_EMAIL like backup.sh does.
#
# Not configured is a FAILURE, not a quiet skip. A skipped off-site copy is exactly
# the state that lost the platform, so it must be loud from the first night.
#
# tools/test-offsite-backup.sh runs this against a stand-in rclone, both ways.

set -euo pipefail

SRC=${BACKUP_DEST:-/srv/vent/backups}
MEDIA=${OFFSITE_MEDIA:-/srv/vent/media}
PRIVATE=${OFFSITE_PRIVATE:-/srv/vent/private}
CONF=${OFFSITE_CONF:-/srv/vent/offsite.conf}
REMOTE=${OFFSITE_REMOTE:-offsite}
ENV_FILE=${BACKUP_ENV_FILE:-/srv/vent/backend/.env}
SENDMAIL=${OFFSITE_SENDMAIL:-/usr/sbin/sendmail}

# BACKUP_ALERT_EMAIL, READ from .env, never sourced: .env is Django's file, and
# `DEFAULT_FROM_EMAIL=V-ENT <info@v-ent.co>` is a redirection to bash.
ALERT_TO=${BACKUP_ALERT_EMAIL:-}
if [ -z "$ALERT_TO" ] && [ -r "$ENV_FILE" ]; then
    line=$(grep -m1 '^BACKUP_ALERT_EMAIL=' "$ENV_FILE" || true)
    line=${line#*=}
    line=${line%\"}; line=${line#\"}
    line=${line%\'}; line=${line#\'}
    ALERT_TO=$(printf '%s' "$line" | tr -d '\r')
fi

notify() {
    local subject=$1 body=$2
    [ -n "$ALERT_TO" ] || return 0
    if [ ! -x "$SENDMAIL" ]; then
        echo "$(date -Is) WARNING: cannot alert $ALERT_TO, $SENDMAIL is not installed" >&2
        return 0
    fi
    "$SENDMAIL" -t <<MAILEOF || true
To: $ALERT_TO
From: V-ENT backup <no-reply@v-ent.co>
Subject: $subject

$body

Host:    $(hostname)
When:    $(date -Is)
Log:     /srv/vent/logs/offsite.log
MAILEOF
}

fail() {
    echo "$(date -Is) OFFSITE FAILED: $*" >&2
    notify "V-ENT off-site backup FAILED on $(hostname)" \
        "The nightly copy to the off-site bucket did not complete.

Reason: $*

The backups on the box are still there, but nothing new left the box tonight.
If the box is lost before this is fixed, so is everything since the last good
off-site copy."
    exit 1
}

# Counts files under a local directory. 0 when it does not exist.
local_count() {
    if [ -d "$1" ]; then
        find "$1" -type f | wc -l | tr -d ' '
    else
        echo 0
    fi
}

# Counts objects under a remote path, from `rclone size --json`.
remote_count() {
    local json
    json=$(rclone size "$1" --json 2>/dev/null) || { echo 0; return 0; }
    printf '%s' "$json" | { grep -oE '"count":[0-9]+' || true; } | cut -d: -f2 | tail -1
}

# ------------------------------------------------------------------ preconditions
command -v rclone >/dev/null 2>&1 \
    || fail "rclone is not installed. Fix: sudo apt-get install -y rclone"

# `grep -c ... || true`, never `grep -q`: -q exits on the first match, rclone
# takes SIGPIPE, and pipefail turns a FOUND remote into a failure (the backup.sh
# trap of 8 September).
FOUND=$(rclone listremotes 2>/dev/null | grep -cx "${REMOTE}:" || true)
[ "${FOUND:-0}" -ge 1 ] \
    || fail "there is no rclone remote called '$REMOTE'. Fix: rclone config (name $REMOTE, type b2, the bucket's key)"

[ -r "$CONF" ] \
    || fail "$CONF is missing. Fix: echo 'BUCKET=<bucket name>' > $CONF"
BUCKET=$(grep -m1 '^BUCKET=' "$CONF" | cut -d= -f2- | tr -d '\r"'"'"' ' || true)
[ -n "$BUCKET" ] || fail "$CONF has no BUCKET= line. Fix: echo 'BUCKET=<bucket name>' > $CONF"

# `|| true` inside: under pipefail a missing $SRC would otherwise end the script
# here with no message at all, which is the one outcome this file exists to stop.
NEWEST=$({ find "$SRC" -maxdepth 1 -type f -name 'db-*.sql.gz' -printf '%T@ %p\n' 2>/dev/null || true; } \
    | sort -n | tail -1 | cut -d' ' -f2-)
[ -n "$NEWEST" ] \
    || fail "no db-*.sql.gz in $SRC, so backup.sh has not produced a dump. Read /srv/vent/logs/backup.log"
NAME=$(basename "$NEWEST")
LOCAL_SIZE=$(stat -c%s "$NEWEST")

# ------------------------------------------------------------------------ copy
rclone copy "$SRC" "$REMOTE:$BUCKET/db" --include 'db-*.sql.gz' --transfers 4 -q \
    || fail "copying the dumps to $REMOTE:$BUCKET/db failed"

for pair in "media:$MEDIA" "private:$PRIVATE"; do
    dest=${pair%%:*}
    dir=${pair#*:}
    [ -d "$dir" ] || continue
    rclone sync "$dir" "$REMOTE:$BUCKET/$dest" --transfers 8 --fast-list -q \
        || fail "syncing $dir to $REMOTE:$BUCKET/$dest failed"
done

# ---------------------------------------------------------- verify the CONTENTS
REMOTE_SIZE=$({ rclone lsl "$REMOTE:$BUCKET/db/$NAME" 2>/dev/null || true; } | awk 'NR==1 {print $1}')
[ "${REMOTE_SIZE:-}" = "$LOCAL_SIZE" ] \
    || fail "$NAME is $LOCAL_SIZE bytes here but '${REMOTE_SIZE:-nothing}' in $REMOTE:$BUCKET/db"

MEDIA_HERE=$(local_count "$MEDIA")
MEDIA_THERE=$(remote_count "$REMOTE:$BUCKET/media")
[ "${MEDIA_THERE:-0}" -ge "$MEDIA_HERE" ] \
    || fail "the box has $MEDIA_HERE media files but the bucket holds ${MEDIA_THERE:-0}"

PRIVATE_HERE=$(local_count "$PRIVATE")
PRIVATE_THERE=$(remote_count "$REMOTE:$BUCKET/private")
[ "${PRIVATE_THERE:-0}" -ge "$PRIVATE_HERE" ] \
    || fail "the box has $PRIVATE_HERE private files but the bucket holds ${PRIVATE_THERE:-0}"

echo "$(date -Is) offsite ok: $NAME ($LOCAL_SIZE bytes) in $BUCKET/db, media $MEDIA_THERE files, private $PRIVATE_THERE files"
