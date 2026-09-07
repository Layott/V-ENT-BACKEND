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
DEST=/srv/vent/backups
ENV_FILE=/srv/vent/backend/.env

# The floor a real dump of this platform clears comfortably. Not a guess: the
# dump taken by hand on 7 September held 159 tables and 83 INSERT statements.
# Set well below that so a quiet week does not trip it, and well above zero so a
# schema-only or truncated dump does.
MIN_TABLES=100
MIN_INSERTS=20

fail() {
    # Loudly, and on stderr, so cron mails it and the log shows it as a failure
    # rather than as a line somebody has to notice the absence of.
    echo "$(date -Is) BACKUP FAILED: $*" >&2
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
    line=${line%'}; line=${line#'}
    printf '%s' "$line"
}

DB_NAME=$(read_env DB_NAME) || fail "DB_NAME missing from $ENV_FILE"
DB_USER=$(read_env DB_USER) || fail "DB_USER missing from $ENV_FILE"
DB_PASSWORD=$(read_env DB_PASSWORD) || fail "DB_PASSWORD missing from $ENV_FILE"

[ -n "$DB_NAME" ] || fail "DB_NAME is empty in $ENV_FILE"
[ -n "$DB_USER" ] || fail "DB_USER is empty in $ENV_FILE"
[ -n "$DB_PASSWORD" ] || fail "DB_PASSWORD is empty in $ENV_FILE"

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
    # `grep -F` on a plain fixed string, and the table name concatenated in
    # rather than interpolated inside a quoted pattern.
    #
    # It was written as "CREATE TABLE \`$TABLE\`", and a backtick inside a
    # DOUBLE-quoted shell string is command substitution. So the check ran the
    # table name as a command, matched nothing, and deleted a perfectly good
    # dump while reporting that vent_auth_users was missing. The refusal was
    # right; the reason it gave was not.
    zcat "$DB_FILE" | grep -qF "CREATE TABLE ${BT}${TABLE}${BT}" \
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
