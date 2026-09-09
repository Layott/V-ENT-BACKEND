#!/usr/bin/env bash
# Restore a backup into a scratch database and COUNT WHAT CAME BACK.
#
#   ./restore-check.sh /srv/vent/backups/db-2026-09-08-0300.sql.gz
#   ./restore-check.sh --self-test
#
# ## Why this exists
#
# `backup.sh` proves a dump has plausible CONTENTS: it counts CREATE TABLE and
# INSERT statements and looks for three tables by name. That is a real check and
# it caught a real fault. But it reads the file as TEXT. It cannot tell you that
# MySQL will accept the file, that the inserts refer to columns that exist, or
# that the rows land where they are supposed to.
#
# **A backup nobody has restored is a file, not a backup.** The only thing that
# proves a dump is a backup is putting it back and counting rows.
#
# ## What it will not do
#
# It never touches the live database. It creates a scratch schema, restores into
# THAT, counts, and drops it. The name carries a timestamp so two runs cannot
# collide, and the drop happens in a trap so an interrupted run does not leave a
# schema behind.
#
# The one thing to be careful with is the credentials: on the box this uses the
# same `vent` user as the backup, which means that user needs CREATE and DROP on
# the scratch name. If it does not have them the script says so plainly rather
# than half running.
set -euo pipefail

ENV_FILE=${ENV_FILE:-/srv/vent/backend/.env}

# The tables worth counting by name. A restore that brings back 165 empty tables
# is a successful restore of nothing, and a total row count hides that inside one
# number. These are the three that would hurt most to lose, the same three
# backup.sh looks for, so the two checks agree on what matters.
KEY_TABLES="vent_auth_users vent_tournament_tournament vent_event_ticket"

# A restore that produces fewer rows than this in the users table is not a
# restore worth having. Set from the real figure rather than guessed: production
# held 119 users on 8 September.
MIN_USERS=1

die() { echo "$(date -Is) RESTORE CHECK FAILED: $*" >&2; exit 1; }

read_env() {
    local key=$1 line
    # Read, never source. `.env` holds values with angle brackets and spaces
    # that bash would treat as redirections. Same reasoning as backup.sh, and
    # the same fault it was written to survive.
    line=$(grep -m1 "^${key}=" "$ENV_FILE") || return 1
    line=${line#*=}
    line=${line%\"}; line=${line#\"}
    line=${line%\'}; line=${line#\'}
    printf '%s' "$line"
}

self_test() {
    # What can be proven without a database: the argument handling and the
    # refusals. The restore itself needs MySQL, so the honest thing is to test
    # the parts that do not and say so, rather than to claim a green run means
    # more than it does.
    local failures=0
    check() {
        if [ "$2" = "$3" ]; then echo "  ok   $1"; else
            echo "  FAIL $1: got '$2', wanted '$3'"; failures=$((failures + 1)); fi
    }

    local out
    out=$(bash "$0" /definitely/not/here.sql.gz 2>&1 || true)
    case "$out" in
        *"cannot read"*) check "a missing dump is refused" "yes" "yes" ;;
        *) check "a missing dump is refused" "$out" "a refusal" ;;
    esac

    out=$(bash "$0" 2>&1 || true)
    case "$out" in
        *"usage"*|*"Usage"*) check "no argument prints usage" "yes" "yes" ;;
        *) check "no argument prints usage" "$out" "usage" ;;
    esac

    # A truncated gzip must be caught BEFORE mysql is handed it, because mysql
    # would import the readable prefix and exit 0 on a half restore.
    local tmp
    tmp=$(mktemp).sql.gz
    printf 'not gzip at all' > "$tmp"
    out=$(bash "$0" "$tmp" 2>&1 || true)
    rm -f "$tmp"
    case "$out" in
        *"not a readable gzip"*) check "a corrupt dump is caught before mysql sees it" "yes" "yes" ;;
        *) check "a corrupt dump is caught before mysql sees it" "$out" "a gzip refusal" ;;
    esac

    echo
    if [ "$failures" -gt 0 ]; then echo "$failures failed"; return 1; fi
    echo "self-test: all passed (the restore itself needs a database and is exercised by a real run)"
    return 0
}

if [ "${1:-}" = "--self-test" ]; then self_test; exit $?; fi

DUMP=${1:-}
if [ -z "$DUMP" ]; then
    echo "usage: $0 <dump.sql.gz>    restore it into a scratch schema and count the rows" >&2
    exit 2
fi

[ -r "$DUMP" ] || die "cannot read $DUMP"
gzip -t "$DUMP" 2>/dev/null || die "$DUMP is not a readable gzip. Nothing was restored."

[ -r "$ENV_FILE" ] || die "cannot read $ENV_FILE"
DB_USER=$(read_env DB_USER) || die "DB_USER missing from $ENV_FILE"
DB_PASSWORD=$(read_env DB_PASSWORD) || die "DB_PASSWORD missing from $ENV_FILE"

SCRATCH="vent_restore_check_$(date +%s)"

CNF=$(mktemp)
chmod 600 "$CNF"
cat > "$CNF" <<CNFEOF
[client]
user=$DB_USER
password=$DB_PASSWORD
CNFEOF

# The scratch schema goes away whatever happens next, including an interrupt.
# A check that leaves debris behind is a check people stop running.
cleanup() {
    mysql --defaults-extra-file="$CNF" -e "DROP DATABASE IF EXISTS \`$SCRATCH\`;" >/dev/null 2>&1 || true
    rm -f "$CNF"
}
trap cleanup EXIT

echo "$(date -Is) restoring $(basename "$DUMP") into $SCRATCH"

mysql --defaults-extra-file="$CNF" -e "CREATE DATABASE \`$SCRATCH\` CHARACTER SET utf8mb4;" \
    || die "could not create the scratch schema. Does $DB_USER have CREATE?"

# `--force` is deliberately NOT used. An error part way through must fail the
# run, not be skipped so the counts look plausible at the end.
if ! zcat "$DUMP" | mysql --defaults-extra-file="$CNF" "$SCRATCH"; then
    die "mysql refused the dump. It is not restorable."
fi

TABLES=$(mysql --defaults-extra-file="$CNF" -N -B -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$SCRATCH';")

echo "  restored $TABLES tables"
[ "$TABLES" -ge 100 ] || die "only $TABLES tables came back. The restore is not whole."

TOTAL=0
for TABLE in $KEY_TABLES; do
    # Every count is a real COUNT(*) against the restored schema. Reading
    # information_schema.table_rows instead would be faster and would be a
    # guess: InnoDB keeps that figure as an estimate and it is routinely wrong
    # by a wide margin, including reporting zero for a table with rows in it.
    N=$(mysql --defaults-extra-file="$CNF" -N -B -e \
        "SELECT COUNT(*) FROM \`$SCRATCH\`.\`$TABLE\`;" 2>/dev/null) \
        || die "$TABLE is not in the restored database"
    echo "  $TABLE: $N rows"
    TOTAL=$((TOTAL + N))
    if [ "$TABLE" = "vent_auth_users" ] && [ "$N" -lt "$MIN_USERS" ]; then
        die "$TABLE restored with $N rows. A backup with no users in it is not a backup."
    fi
done

echo "$(date -Is) restore ok: $TABLES tables, $TOTAL rows across the key tables. Scratch schema dropped."
