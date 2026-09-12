#!/usr/bin/env bash
# Nightly database + media backup.
#
#   Cron: 0 3 * * * /srv/vent/backend/deploy/backup.sh >> /srv/vent/logs/backup.log 2>&1
#
# CEO, 7 September 2026: "Your nightly backup has been producing nothing. - fix
# this."
#
# ## What was wrong
#
# The old version ran:
#
#     mysqldump --single-transaction --routines --triggers vent | gzip > db.sql.gz
#
# with NO USER AND NO PASSWORD. It died on every run with
#
#     Access denied for user 'vent'@'localhost' (using password: NO)
#
# and because `set -e` was on and the dump was the first command, it never
# reached the media archive either. So the nightly cron this file documents has
# been producing NOTHING since it was written, and nobody noticed, because a
# cron that fails writes to a log nobody reads.
#
# The credentials were in `/srv/vent/backend/.env` the entire time.
#
# ## The rule this file now follows
#
# **A backup is verified by its CONTENTS, never by its exit code.** A dump that
# holds only a schema and a dump that holds the whole platform both exit 0 and
# both look like a file on disk. So this counts the tables and the rows and
# refuses to keep a file that has neither, which is the difference between
# having a backup and believing you have one.
#
# See `feedback_mysqldump_fails_silently` and
# `project_backup_script_broken`.
set -euo pipefail

STAMP=$(date +%F-%H%M)
# Overridable so the freshness check and the refusals can be exercised somewhere
# that is not the production box. The defaults are the real paths, so nothing
# about a normal cron run changes.
DEST=${BACKUP_DEST:-/srv/vent/backups}
ENV_FILE=${BACKUP_ENV_FILE:-/srv/vent/backend/.env}

# The floor a real dump of this platform clears comfortably. Not a guess: the
# dump taken by hand on 7 September held 159 tables and 83 INSERT statements.
# Set well below that so a quiet week does not trip it, and well above zero so a
# schema-only or truncated dump does.
MIN_TABLES=100
MIN_INSERTS=20

# Where a failure gets sent. Read from .env as BACKUP_ALERT_EMAIL so it can be
# changed without touching this file; there is deliberately no default address
# baked in, because a wrong default is worse than an absent one: it looks
# configured and goes nowhere.
# Taken from the environment FIRST, and only then from .env further down.
#
# The order matters for one case, and it is the worst case: if `.env` is
# unreadable the script fails before it could ever learn an address, so the one
# failure that means the most would be the one that told nobody. Setting
# BACKUP_ALERT_EMAIL in the crontab line itself survives that.
ALERT_TO=${BACKUP_ALERT_EMAIL:-}

# ---------------------------------------------------------------- telling somebody
#
# The comment this replaces claimed "so cron mails it". It does not, and that is
# the whole bug. The crontab line is
#
#     0 3 * * * /srv/vent/backend/deploy/backup.sh >> /srv/vent/logs/backup.log 2>&1
#
# and `2>&1` sends stderr into the log FILE. Cron mails a job's OUTPUT, and this
# job produces none, so cron has nothing to send whether it succeeded or failed.
# There is no MAILTO in the crontab either. So a failure has always written one
# line into a file nobody opens, which is exactly the condition that let the
# broken backup run unnoticed for weeks before 7 September.
#
# Sending the mail from inside the script fixes it regardless of how cron is
# configured, which also means it keeps working if somebody changes the
# redirection later.
notify() {
    local subject=$1 body=$2
    [ -n "$ALERT_TO" ] || return 0
    # sendmail rather than `mail`, because it is what Postfix installs and it
    # takes the headers from the message itself, so there is no argument
    # quoting to get wrong.
    #
    # Checked for rather than assumed: if Postfix is ever removed the alert must
    # say out loud that it could not be sent, not die inside a failure handler
    # and replace the real reason with a missing-file error.
    if [ ! -x /usr/sbin/sendmail ]; then
        echo "$(date -Is) WARNING: cannot alert $ALERT_TO, /usr/sbin/sendmail is not installed" >&2
        return 0
    fi
    /usr/sbin/sendmail -t <<MAILEOF || true
To: $ALERT_TO
From: V-ENT backup <no-reply@v-ent.co>
Subject: $subject

$body

Host:   $(hostname)
When:   $(date -Is)
Log:    /srv/vent/logs/backup.log
Backups: $DEST
MAILEOF
}

fail() {
    # Loudly, on stderr AND by mail. The log line is for whoever goes looking;
    # the mail is what makes somebody go looking in the first place.
    echo "$(date -Is) BACKUP FAILED: $*" >&2
    notify "V-ENT backup FAILED on $(hostname)" \
        "The nightly backup did not complete.

Reason: $*

Nothing was kept for this run. The most recent good backup is whatever is
already in $DEST, which may now be a day or more old."
    exit 1
}

[ -r "$ENV_FILE" ] || fail "cannot read $ENV_FILE"

# The credentials, from the same file Django reads.
#
# READ, never SOURCED. `.env` is a Django environment file, not a shell script,
# and sourcing it runs it: `DEFAULT_FROM_EMAIL=V-ENT <info@v-ent.co>` made bash
# die with "syntax error near unexpected token `newline'" because the angle
# brackets are redirections. Django's own parser does not care, so the file is
# perfectly valid and the backup was the only thing that broke on it.
#
# So one key at a time, value taken literally, quotes stripped, and nothing in
# the file is ever executed.
read_env() {
    local key=$1
    local line
    line=$(grep -m1 "^${key}=" "$ENV_FILE") || return 1
    line=${line#*=}
    # Strip one layer of surrounding quotes, if present.
    line=${line%\"}; line=${line#\"}
    # The backslashes matter. Written as ${line%'} the single quote OPENS a
    # quoted run that swallows the rest of the line, so the pattern becomes the
    # literal string "}; line=${line#" and a single-quoted value keeps its
    # quotes. It parses, it never errors, and it silently does nothing, which is
    # why it survived. Nothing in .env is single quoted today, so this was
    # latent rather than broken: the day somebody writes DB_PASSWORD='...' the
    # backup starts failing to authenticate and the reason is invisible.
    line=${line%\'}; line=${line#\'}
    printf '%s' "$line"
}

DB_NAME=$(read_env DB_NAME) || fail "DB_NAME missing from $ENV_FILE"
DB_USER=$(read_env DB_USER) || fail "DB_USER missing from $ENV_FILE"
DB_PASSWORD=$(read_env DB_PASSWORD) || fail "DB_PASSWORD missing from $ENV_FILE"

[ -n "$DB_NAME" ] || fail "DB_NAME is empty in $ENV_FILE"
[ -n "$DB_USER" ] || fail "DB_USER is empty in $ENV_FILE"
[ -n "$DB_PASSWORD" ] || fail "DB_PASSWORD is empty in $ENV_FILE"

# Read AFTER the definition of read_env, and allowed to be absent: a missing
# alert address must not stop the backup from running. It does get said out
# loud on every run, though, because "nobody is being told" is precisely the
# state that needs to be visible rather than assumed.
if [ -z "$ALERT_TO" ]; then
    ALERT_TO=$(read_env BACKUP_ALERT_EMAIL 2>/dev/null || true)
fi
if [ -z "$ALERT_TO" ]; then
    echo "$(date -Is) WARNING: BACKUP_ALERT_EMAIL is not set in $ENV_FILE, so a failure will tell nobody" >&2
fi

# ------------------------------------------------------- the dead man check
#
#   backup.sh --check-freshness
#
# The failure mail above covers a backup that RAN and went wrong. It cannot
# cover the other half: a backup that never ran at all. A cron that is removed,
# a box that was down at 03:00, a disk that filled, a path that moved after a
# deploy. All of those are silent, and the failure mail is silent with them,
# because nothing executed to send it.
#
# So this mode looks at the newest dump and complains if it is too old. Run it
# from a SECOND cron entry at a different time of day, so the two do not fail
# together for the same reason.
#
# 30 hours rather than 24: a nightly job plus an hour of drift plus a slow run
# must not page anybody, and 30 still catches a single missed night.
if [ "${1:-}" = "--check-freshness" ]; then
    MAX_AGE_HOURS=${2:-30}
    NEWEST=$(find "$DEST" -maxdepth 1 -name 'db-*.sql.gz' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -z "$NEWEST" ]; then
        fail "there is no database backup at all in $DEST"
    fi
    AGE_SECONDS=$(( $(date +%s) - $(stat -c %Y "$NEWEST") ))
    AGE_HOURS=$(( AGE_SECONDS / 3600 ))
    if [ "$AGE_HOURS" -gt "$MAX_AGE_HOURS" ]; then
        fail "the newest backup is $AGE_HOURS hours old ($(basename "$NEWEST")), which means a run was missed. Nothing failed, so nothing reported it."
    fi

    # Age is not enough, and this is not hypothetical. There is a second, older
    # backup script on this box at /srv/vent/deploy/backup.sh which runs
    # mysqldump with no credentials, fails with "Access denied", and still
    # leaves a ~20 byte db-*.sql.gz in this very directory. Anybody running it
    # by hand plants a file that is BOTH the newest and empty, and a check that
    # only read the clock would call that fresh and say nothing.
    #
    # 10 KB is far below a real dump here (they run 150 KB and up compressed)
    # and far above an empty or error-only one.
    MIN_BYTES=${3:-10240}
    SIZE=$(stat -c %s "$NEWEST")
    if [ "$SIZE" -lt "$MIN_BYTES" ]; then
        fail "the newest backup $(basename "$NEWEST") is only $SIZE bytes, which is not a backup. Something wrote a file that looks like one. The working script is /srv/vent/backend/deploy/backup.sh."
    fi
    # And it has to actually be a gzip that unpacks, because a truncated file
    # has a plausible size and no contents.
    if ! gzip -t "$NEWEST" 2>/dev/null; then
        fail "the newest backup $(basename "$NEWEST") is $SIZE bytes but is not a readable gzip, so it would not restore."
    fi

    echo "$(date -Is) freshness ok: $(basename "$NEWEST") is $AGE_HOURS hours old and $SIZE bytes"
    exit 0
fi

mkdir -p "$DEST"
cd "$DEST"

DB_FILE="db-$STAMP.sql.gz"
MEDIA_FILE="media-$STAMP.tar.gz"

# ---------------------------------------------------------------- database
#
# The password goes in via a temporary defaults file rather than on the command
# line, so it never appears in `ps` output for other users on the box, and
# mysqldump stops warning about it.
CNF=$(mktemp)
chmod 600 "$CNF"
trap 'rm -f "$CNF"' EXIT
cat > "$CNF" <<CNFEOF
[client]
user=$DB_USER
password=$DB_PASSWORD
CNFEOF

# `--no-tablespaces`: dumping tablespace metadata needs the PROCESS privilege,
# which this user does not have and does not need. Without the flag mysqldump
# prints an error and exits non-zero even though the data dumped fine.
mysqldump --defaults-extra-file="$CNF" \
    --single-transaction --routines --triggers --no-tablespaces \
    "$DB_NAME" | gzip > "$DB_FILE" || fail "mysqldump failed"

rm -f "$CNF"
trap - EXIT

# ------------------------------------------------------- verify the CONTENTS
#
# This is the part the old script had no version of at all.
TABLES=$(zcat "$DB_FILE" | grep -c '^CREATE TABLE' || true)
INSERTS=$(zcat "$DB_FILE" | grep -c '^INSERT INTO' || true)

if [ "$TABLES" -lt "$MIN_TABLES" ]; then
    rm -f "$DB_FILE"
    fail "only $TABLES tables in the dump, expected at least $MIN_TABLES. Kept nothing."
fi

if [ "$INSERTS" -lt "$MIN_INSERTS" ]; then
    rm -f "$DB_FILE"
    fail "only $INSERTS INSERT statements, expected at least $MIN_INSERTS. That is a schema with no data. Kept nothing."
fi

# The tables that would hurt most to lose, named rather than counted. A dump
# can clear both thresholds above and still have missed the one table somebody
# actually needs back.
#
# A literal backtick, held in a variable so no quoting style has to survive it.
BT='`'
for TABLE in vent_auth_users vent_event_ticket vent_tournament_tournament; do
    # `grep -cF`, never `grep -q`, and `|| true` on the pipeline.
    #
    # Two separate traps here, and this check hit BOTH:
    #
    # 1. It was written as "CREATE TABLE \`$TABLE\`". A backtick inside a
    #    DOUBLE-quoted shell string is command substitution, so it ran the
    #    table name as a command. Hence `BT` above: a literal backtick in a
    #    variable, which no quoting style has to survive.
    #
    # 2. `grep -q` EXITS ON THE FIRST MATCH. That closes the pipe, `zcat` takes
    #    SIGPIPE and exits 141, and `set -o pipefail` at the top of this file
    #    turns the whole pipeline non-zero. So the check reported the table
    #    missing precisely BECAUSE it had found it, and deleted a good backup
    #    to say so. Counting reads the stream to the end and never signals.
    FOUND=$(zcat "$DB_FILE" | grep -cF "CREATE TABLE ${BT}${TABLE}${BT}" || true)
    [ "${FOUND:-0}" -ge 1 ] \
        || { rm -f "$DB_FILE"; fail "$TABLE is not in the dump. Kept nothing."; }
done

# ------------------------------------------------------------------- media
#
# Uploaded pictures and the private KYC directory. Never reached by the old
# script, because it died before this line on every single run.
tar czf "$MEDIA_FILE" -C /srv/vent media private || fail "media archive failed"
tar tzf "$MEDIA_FILE" > /dev/null 2>&1 || {
    rm -f "$MEDIA_FILE"
    fail "the media archive is not readable. Kept nothing."
}

# --------------------------------------------------------------- housekeeping
find "$DEST" -name '*.gz' -mtime +14 -delete || true

DB_SIZE=$(du -h "$DB_FILE" | cut -f1)
MEDIA_SIZE=$(du -h "$MEDIA_FILE" | cut -f1)

echo "$(date -Is) backup ok: $DB_FILE ($DB_SIZE, $TABLES tables, $INSERTS inserts), $MEDIA_FILE ($MEDIA_SIZE)"

# A backup on the same disk is not a backup. Nothing here pulls these off the
# box yet, and that is the remaining half of this problem.
