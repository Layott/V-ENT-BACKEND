#!/usr/bin/env bash
# Runs deploy/vent-offsite-backup.sh against a stand-in rclone whose "bucket" is a
# local folder, both ways: it must refuse loudly (non-zero, the fix named, an alert
# mailed) when the remote, the bucket or the dump is missing, or when what landed in
# the bucket does not match the box; and it must pass and say "offsite ok" when the
# copy is whole. Written 14 November 2026, the day InterServer deleted the box and
# every backup on it.
set -u
SCRIPT="${1:-deploy/vent-offsite-backup.sh}"
SCRIPT=$(cd "$(dirname "$SCRIPT")" && pwd)/$(basename "$SCRIPT")
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/backups" "$T/media/profile_pictures" "$T/private/kyc" "$T/bucket"

# ------------------------------------------------------------ stand-in rclone
cat > "$T/bin/rclone" <<'EOF'
#!/usr/bin/env bash
# Remote "offsite:" maps to $STUB_ROOT. Only the verbs the script uses.
set -u
to_path() { printf '%s' "$STUB_ROOT/${1#offsite:}"; }
verb=$1; shift
case "$verb" in
  listremotes)
    [ -f "$STUB_ROOT/.configured" ] && echo "offsite:"
    exit 0 ;;
  copy)
    src=$1 dst=$(to_path "$2"); shift 2
    pat='*'
    while [ $# -gt 0 ]; do [ "$1" = --include ] && pat=$2; shift; done
    mkdir -p "$dst"
    for f in "$src"/$pat; do [ -f "$f" ] && cp "$f" "$dst/"; done
    if [ -n "${STUB_TRUNCATE:-}" ]; then
      for f in "$dst"/*; do head -c 5 "$f" > "$f.t" && mv "$f.t" "$f"; done
    fi
    exit 0 ;;
  sync)
    src=$1 dst=$(to_path "$2")
    rm -rf "$dst"; mkdir -p "$dst"
    cp -r "$src"/. "$dst"/
    if [ -n "${STUB_DROP_MEDIA:-}" ] && [ "${2##*/}" = media ]; then
      find "$dst" -type f | head -1 | xargs rm -f
    fi
    exit 0 ;;
  lsl)
    p=$(to_path "$1")
    [ -f "$p" ] || exit 3
    echo "$(wc -c < "$p" | tr -d ' ') 2026-11-14 03:45:00.000000000 $(basename "$p")"
    exit 0 ;;
  size)
    p=$(to_path "$1")
    n=$(find "$p" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "{\"count\":$n,\"bytes\":0}"
    exit 0 ;;
esac
exit 9
EOF
chmod +x "$T/bin/rclone"

# ------------------------------------------------------------ stand-in sendmail
cat > "$T/bin/sendmail" <<EOF
#!/usr/bin/env bash
cat >> "$T/mail.txt"
EOF
chmod +x "$T/bin/sendmail"

# ------------------------------------------------------------------- fixtures
printf 'dump of the night before\n' | gzip > "$T/backups/db-2026-11-13-0300.sql.gz"
touch -d '1 day ago' "$T/backups/db-2026-11-13-0300.sql.gz"
printf 'CREATE TABLE `vent_auth_users` (...);\nINSERT INTO ...;\n' | gzip > "$T/backups/db-2026-11-14-0300.sql.gz"
echo png > "$T/media/profile_pictures/a.png"
echo png > "$T/media/profile_pictures/b.png"
echo jpg > "$T/private/kyc/id.jpg"
echo 'BACKUP_ALERT_EMAIL=alerts@example.invalid' > "$T/env"

run() {
    # $1 = expected outcome (ok|fail), $2 = what the output must contain, $3 = label; env set by caller
    local want=$1 needle=$2 label=$3 out code
    : > "$T/mail.txt"
    out=$(PATH="$T/bin:$PATH" STUB_ROOT="$T/bucket" \
          BACKUP_DEST="$T/backups" OFFSITE_MEDIA="$T/media" OFFSITE_PRIVATE="$T/private" \
          OFFSITE_CONF="$T/offsite.conf" BACKUP_ENV_FILE="$T/env" OFFSITE_SENDMAIL="$T/bin/sendmail" \
          bash "$SCRIPT" 2>&1); code=$?
    if [ "$want" = ok ] && [ "$code" -ne 0 ]; then echo "FAIL: $label: exited $code: $out"; fails=$((fails+1)); return; fi
    if [ "$want" = fail ] && [ "$code" -eq 0 ]; then echo "FAIL: $label: exited 0: $out"; fails=$((fails+1)); return; fi
    case "$out" in *"$needle"*) ;; *) echo "FAIL: $label: output lacks '$needle': $out"; fails=$((fails+1)); return ;; esac
    if [ "$want" = fail ] && ! grep -q 'Subject: V-ENT off-site backup FAILED' "$T/mail.txt"; then
        echo "FAIL: $label: refused but mailed nobody"; fails=$((fails+1)); return
    fi
    echo "ok: $label"
}
fails=0

# 1. no remote configured
run fail "Fix: rclone config" "no rclone remote"

# 2. remote but no bucket file
touch "$T/bucket/.configured"
run fail "offsite.conf is missing" "no bucket file"

# 3. a bucket file without a BUCKET= line
echo '# nothing' > "$T/offsite.conf"
run fail "has no BUCKET= line" "empty bucket file"

# 4. the whole thing, done right
echo 'BUCKET=vent-offsite-backups' > "$T/offsite.conf"
run ok "offsite ok: db-2026-11-14-0300.sql.gz" "configured, whole copy"
[ -f "$T/bucket/vent-offsite-backups/db/db-2026-11-14-0300.sql.gz" ] \
    || { echo "FAIL: the newest dump is not in the bucket"; fails=$((fails+1)); }
[ -f "$T/bucket/vent-offsite-backups/private/kyc/id.jpg" ] \
    || { echo "FAIL: the private folder is not in the bucket"; fails=$((fails+1)); }

# 5. what landed is not what left
rm -rf "$T/bucket/vent-offsite-backups"
STUB_TRUNCATE=1 run fail "bytes here but" "truncated upload"

# 6. a media file missing from the bucket
rm -rf "$T/bucket/vent-offsite-backups"
STUB_DROP_MEDIA=1 run fail "media files but the bucket holds" "media short in the bucket"

# 7. backup.sh never produced a dump
rm -f "$T/backups"/db-*.sql.gz
run fail "has not produced a dump" "no dump on the box"

# 8. the backups folder itself is gone: still a message, never a silent exit
rm -rf "$T/backups"
run fail "has not produced a dump" "no backups folder"

[ "$fails" -eq 0 ] && echo "offsite backup: passed" || { echo "offsite backup: $fails failed"; exit 1; }
