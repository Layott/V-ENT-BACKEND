#!/bin/bash
# mail-relay.sh - point the box's Postfix at an outbound relay. Run with sudo.
#
#   sudo /srv/vent/backend/deploy/mail-relay.sh gmail
#   sudo /srv/vent/backend/deploy/mail-relay.sh gmail --test you@example.com
#   sudo /srv/vent/backend/deploy/mail-relay.sh --status
#
# History. The first version lived only on the old server, at
# /srv/vent/deploy/mail-relay.sh, and was never committed. InterServer deleted that
# server on 14 November 2026 and the script went with it. This one is rewritten from
# what the project recorded about the old one (reference_vps_and_deploy, "mail
# relay"), and it lives in git so the next box gets it from a clone.
#
# How V-ENT mail leaves the box:
#   Django -> 127.0.0.1:25 (local Postfix; EMAIL_HOST in .env is literally that)
#          -> the relay below -> the world.
# So swapping providers is a Postfix change, never a Django one, and this script is
# the only supported way to make it.
#
# Providers:
#   gmail   [smtp.gmail.com]:587, Gmail USER SMTP, signed in as info@v-ent.co with an
#           app password (2-Step Verification must be on for that mailbox). About 2000
#           messages a day. Google's DKIM (google._domainkey) signs it, which is what
#           keeps it out of spam under DMARC p=quarantine.
#           NOT smtp-relay.gmail.com: that answers "550-5.7.0 SMTP relay isn't
#           supported for unmanaged work accounts" on this tier, whatever we configure.
#   brevo   [smtp-relay.brevo.com]:587, 300 a day. Only ever a fallback.
#
# Credentials: /etc/vent/mail-relay/<provider>.cred, owned by root, mode 600:
#   RELAY_USER=info@v-ent.co
#   RELAY_PASSWORD=<the app password>
# Read one key at a time, never sourced. A provider with no credential is REFUSED:
# a relay without one does not fail loudly, it defers for five days and then
# bounces, which looks exactly like nothing happening.
#
# The route and the sender are ONE change. relayhost and the sender rewrite
# (anything@v-ent.co -> info@v-ent.co, which Gmail requires) are always written
# together: a rewrite once staged without its relay handed a provider a From it was
# not allowed to send, and two real emails were lost.
#
# Any OTHER provider that has a credential on file becomes the fallback, used only
# when the primary is unreachable (Postfix's smtp_fallback_relay), never on quota.

set -euo pipefail

CRED_DIR=${MAIL_RELAY_CRED_DIR:-/etc/vent/mail-relay}
GENERIC=/etc/postfix/vent-generic
SASL=/etc/postfix/sasl_passwd
SENDER=info@v-ent.co

die() { echo "mail-relay: $*" >&2; exit 1; }

host_for() {
    case "$1" in
        gmail) echo '[smtp.gmail.com]:587' ;;
        brevo) echo '[smtp-relay.brevo.com]:587' ;;
        *) return 1 ;;
    esac
}

read_cred() {
    # read_cred <file> <KEY>: the value, quotes and CR stripped, nothing executed.
    local line
    line=$(grep -m1 "^$2=" "$1" || true)
    [ -n "$line" ] || return 1
    line=${line#*=}
    line=${line%\"}; line=${line#\"}
    line=${line%\'}; line=${line#\'}
    printf '%s' "$line" | tr -d '\r'
}

status() {
    command -v postconf >/dev/null 2>&1 || die "postfix is not installed"
    echo "relayhost:      $(postconf -h relayhost)"
    echo "fallback:       $(postconf -h smtp_fallback_relay)"
    echo "generic map:    $(postconf -h smtp_generic_maps)"
    echo "listening on:   $(postconf -h inet_interfaces)"
    echo "queued:         $(mailq 2>/dev/null | grep -cE '^[0-9A-F]{6,}' || true)"
}

# ------------------------------------------------------------------ arguments
[ $# -ge 1 ] || die "usage: mail-relay.sh <gmail|brevo> [--test address] | --status"
if [ "$1" = --status ]; then status; exit 0; fi

PROVIDER=$1; shift
TEST_TO=
while [ $# -gt 0 ]; do
    case "$1" in
        --test) TEST_TO=${2:-}; [ -n "$TEST_TO" ] || die "--test needs an address"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

PRIMARY_HOST=$(host_for "$PROVIDER") || die "unknown provider '$PROVIDER' (gmail or brevo)"

# The credential is checked before anything is touched, and before the root check,
# so the refusal names the real problem on any machine.
PRIMARY_CRED="$CRED_DIR/$PROVIDER.cred"
[ -r "$PRIMARY_CRED" ] || die "no credential for $PROVIDER at $PRIMARY_CRED (or it is not readable: run with sudo).
Create it, root only:
  sudo install -d -m 700 $CRED_DIR
  sudo install -m 600 /dev/null $PRIMARY_CRED
  then put two lines in it: RELAY_USER=$SENDER and RELAY_PASSWORD=<app password>
Nothing was changed."
PRIMARY_USER=$(read_cred "$PRIMARY_CRED" RELAY_USER) || die "$PRIMARY_CRED has no RELAY_USER= line. Nothing was changed."
PRIMARY_PASS=$(read_cred "$PRIMARY_CRED" RELAY_PASSWORD) || die "$PRIMARY_CRED has no RELAY_PASSWORD= line. Nothing was changed."
[ -n "$PRIMARY_USER" ] && [ -n "$PRIMARY_PASS" ] || die "$PRIMARY_CRED has an empty value. Nothing was changed."

[ "$(id -u)" -eq 0 ] || die "run it with sudo: it writes /etc/postfix"
command -v postconf >/dev/null 2>&1 || die "postfix is not installed. Fix: sudo apt-get install -y postfix libsasl2-modules"
dpkg -s libsasl2-modules >/dev/null 2>&1 || die "libsasl2-modules is missing, so Postfix cannot sign in to the relay. Fix: sudo apt-get install -y libsasl2-modules"

# ------------------------------------------------------- credentials for Postfix
umask 077
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
printf '%s %s:%s\n' "$PRIMARY_HOST" "$PRIMARY_USER" "$PRIMARY_PASS" > "$TMP"

FALLBACK_HOST=
for other in gmail brevo; do
    [ "$other" = "$PROVIDER" ] && continue
    cred="$CRED_DIR/$other.cred"
    [ -r "$cred" ] || continue
    u=$(read_cred "$cred" RELAY_USER || true)
    p=$(read_cred "$cred" RELAY_PASSWORD || true)
    [ -n "$u" ] && [ -n "$p" ] || continue
    h=$(host_for "$other")
    printf '%s %s:%s\n' "$h" "$u" "$p" >> "$TMP"
    [ -n "$FALLBACK_HOST" ] || FALLBACK_HOST=$h
done

install -m 600 -o root -g root "$TMP" "$SASL"
postmap "hash:$SASL"
chmod 600 "$SASL.db"

# The sender rewrite, in the same change as the route.
printf '/^.*@v-ent\\.co$/ %s\n' "$SENDER" > "$GENERIC"
chmod 644 "$GENERIC"

postconf -e \
    "relayhost = $PRIMARY_HOST" \
    "smtp_fallback_relay = $FALLBACK_HOST" \
    "smtp_sasl_auth_enable = yes" \
    "smtp_sasl_password_maps = hash:$SASL" \
    "smtp_sasl_security_options = noanonymous" \
    "smtp_sasl_tls_security_options = noanonymous" \
    "smtp_tls_security_level = encrypt" \
    "smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt" \
    "smtp_generic_maps = regexp:$GENERIC" \
    "inet_interfaces = loopback-only" \
    "mynetworks = 127.0.0.0/8 [::1]/128"

postfix check || die "postfix check failed after the change; read the lines above"
systemctl restart postfix

echo "mail relay: $PROVIDER via $PRIMARY_HOST as $PRIMARY_USER, fallback: ${FALLBACK_HOST:-none}, senders rewritten to $SENDER"

# ------------------------------------------------------------------- the test
# Proved by the log, not by sendmail's exit code: sendmail returns 0 the moment the
# message is queued, including when the relay will refuse it a second later.
[ -n "$TEST_TO" ] || exit 0

MARK="relaytest-$(date +%s)-$$@v-ent.co"
START=$(date '+%Y-%m-%d %H:%M:%S')
/usr/sbin/sendmail -t <<MAILEOF
To: $TEST_TO
From: V-ENT <no-reply@v-ent.co>
Subject: V-ENT mail relay test ($PROVIDER)
Message-ID: <$MARK>

This message was sent by deploy/mail-relay.sh on $(hostname) to prove the
$PROVIDER relay works. If it reached an inbox, platform mail is flowing.
MAILEOF

QID=
for _ in $(seq 1 30); do
    sleep 2
    QID=$(journalctl --since "$START" --no-pager 2>/dev/null \
        | grep -F "message-id=<$MARK>" | sed -nE 's/.*: ([0-9A-F]{6,}): message-id=.*/\1/p' | tail -1 || true)
    [ -n "$QID" ] || continue
    LINE=$(journalctl --since "$START" --no-pager 2>/dev/null | grep -F "$QID: to=<$TEST_TO>" | tail -1 || true)
    case "$LINE" in
        *status=sent*)     echo "test: SENT to $TEST_TO (queue $QID). Check it landed in the inbox, not spam."; exit 0 ;;
        *status=bounced*)  die "test BOUNCED: $LINE" ;;
        *status=deferred*) die "test DEFERRED (Postfix will retry, but something is wrong): $LINE" ;;
    esac
done
die "no delivery result in the log after 60 seconds (queue id: ${QID:-not found}). Read: journalctl -u postfix --since '$START'"
