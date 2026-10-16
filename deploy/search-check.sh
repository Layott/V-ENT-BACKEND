#!/usr/bin/env bash
# Read the live site the way a search engine does, every day, and say so when
# something would show up in Google Search Console.
#
# CEO, 16 October 2026 (inbox 461), after Search Console listed both submitted
# sitemaps as "Couldn't fetch": "i dont ever want those issues happeniong, so
# let's create automatic checks for them from now on., so that they are alway
# checked."
#
# The check itself is the frontend's scripts/check-search.mjs (robots.txt,
# sitemap.xml, every address in it, coverage of public records, the old sitemap
# names). This runs it against production and mails the result when it fails,
# the same way backup.sh does: cron mails a job's output, and a job whose
# output goes into a log file mails nothing, so the script sends its own.
#
#   0 6 * * * bash /srv/vent/backend/deploy/search-check.sh >> /srv/vent/logs/search-check.log 2>&1
#
# deploy/verify-live.sh runs the same check after every deploy.
#
#   deploy/search-check.sh             check production, mail on failure
#   deploy/search-check.sh --no-mail   check, print, never mail
set -uo pipefail

FRONTEND=${FRONTEND:-/srv/vent/frontend}
ENV_FILE=${SEARCH_ENV_FILE:-/srv/vent/backend/.env}
ORIGIN=${SEARCH_ORIGIN:-https://v-ent.co}
API=${SEARCH_API:-https://api.v-ent.co}

read_env() {
    local key=$1 line
    [ -r "$ENV_FILE" ] || return 1
    line=$(grep -m1 "^${key}=" "$ENV_FILE") || return 1
    printf '%s' "${line#*=}"
}

# The same address the backup failures go to, unless a separate one is set.
ALERT_TO=${SEARCH_ALERT_EMAIL:-$(read_env SEARCH_ALERT_EMAIL || read_env BACKUP_ALERT_EMAIL || true)}

notify() {
    local subject=$1 body=$2
    [ -n "$ALERT_TO" ] || { echo "$(date -Is) WARNING: no SEARCH_ALERT_EMAIL or BACKUP_ALERT_EMAIL, nobody told" >&2; return 0; }
    if [ ! -x /usr/sbin/sendmail ]; then
        echo "$(date -Is) WARNING: cannot alert $ALERT_TO, /usr/sbin/sendmail is not installed" >&2
        return 0
    fi
    /usr/sbin/sendmail -t <<MAILEOF || true
To: $ALERT_TO
From: V-ENT search check <no-reply@v-ent.co>
Subject: $subject

$body

Host:   $(hostname)
When:   $(date -Is)
Log:    /srv/vent/logs/search-check.log
Rerun:  bash /srv/vent/backend/deploy/search-check.sh --no-mail
MAILEOF
}

echo "$(date -Is) search check of $ORIGIN"
OUT=$(node "$FRONTEND/scripts/check-search.mjs" --live "$ORIGIN" --api "$API" 2>&1)
CODE=$?
printf '%s\n' "$OUT" | tail -n 60
if [ "$CODE" -ne 0 ]; then
    if [ "${1:-}" != "--no-mail" ]; then
        notify "V-ENT search check: $(printf '%s\n' "$OUT" | tail -n 1)" \
"Something on $ORIGIN would show up in Google Search Console as a problem.

$(printf '%s\n' "$OUT" | head -n 120)"
    fi
    exit 1
fi
exit 0
