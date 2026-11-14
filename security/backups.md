# Database backups (owner rule R82)

One row per database. Schedule and retention are what the box is set to; "restore rehearsed on"
is the date somebody restored a backup into a scratch database and read rows back. Re-rehearse
every 90 days. `check-security --rule R82` reads this file.

| Database | Provider | Schedule | Retention | Backups go to | Restore rehearsed on | By |
|---|---|---|---|---|---|---|
| mysql (vent) | self-hosted MySQL on the InterServer VPS, dumped by `deploy/backup.sh` | daily 03:00 server time (cron), freshness check 11:00, off-site copy 03:45 (`deploy/vent-offsite-backup.sh`) | 14 days on the box (`find -mtime +14 -delete` in backup.sh); in the bucket, what its lifecycle rule keeps | `/srv/vent/backups/db-YYYY-MM-DD-HHMM.sql.gz` on the box, media alongside, AND the Backblaze B2 bucket (db/, media/, private/) from the rebuilt box on | 2026-09-30 (on the old box, deleted 14 Nov; rehearse again on the new box, from the bucket) | Claude, in session |

## The rehearsal on 30 September 2026

`db-2026-09-30-0300.sql.gz` (183,875 bytes) was restored into the scratch database `test_vent`
(the one database besides `vent` the application user may create) in 11 seconds, read back, and
dropped:

| Table | Restored | Live |
|---|---|---|
| vent_auth_users | 174 | 174 |
| vent_event_ticket | 1427 | 1427 |
| vent_auth_transaction | 27 | 27 |
| vent_tournament_tournament | 12 | 12 |
| vent_event_event | 6 | 6 |
| all tables | 227 | 227 |

How to do it again, on the box:

```
export MYSQL_PWD=$(grep "^DB_PASSWORD=" /srv/vent/backend/.env | cut -d= -f2-)
mysql -u vent -e "DROP DATABASE IF EXISTS test_vent; CREATE DATABASE test_vent CHARACTER SET utf8mb4;"
zcat /srv/vent/backups/db-<latest>.sql.gz | mysql -u vent test_vent
mysql -u vent -N -e "SELECT COUNT(*) FROM test_vent.vent_event_ticket"
mysql -u vent -e "DROP DATABASE test_vent;"
```

## 14 November 2026: the box and every backup on it were deleted

InterServer deleted the server (vps3562738, 162.35.101.16) for an unpaid invoice and confirmed
"no data exist". Every backup above went with it, because every one of them was on that disk. That
was the open item this section had carried since September, and it cost the whole platform's data.

What changed the same day (CEO, 14 November: off-site backups are the one exception to the 17 August
"everything on the VPS" rule):

- `deploy/vent-offsite-backup.sh` copies every dump, and syncs `/srv/vent/media` and
  `/srv/vent/private`, to a Backblaze B2 bucket at 03:45, then checks the newest dump's size in the
  bucket against the box and the file counts against the box. Not configured is a failure that mails
  `BACKUP_ALERT_EMAIL`, never a quiet skip. `tools/test-offsite-backup.sh` (check-all) proves it both
  ways; it was also run against real rclone 1.60 on Ubuntu 24.04 on 14 November.
- `deploy/crontab.vent` holds every scheduled line in git, held by `tools/check-crontab.py`.
- The bucket key is scoped to that one bucket, so the box cannot reach anything else in the account.

Before this row can claim a rehearsal on the new box: restore the newest dump FROM THE BUCKET into
`test_vent`, read the counts back, and write the date here. Steps in
`V-ENT/tasks/vps/REBUILD-2026-11-14.md`.
