# Database backups (owner rule R82)

One row per database. Schedule and retention are what the box is set to; "restore rehearsed on"
is the date somebody restored a backup into a scratch database and read rows back. Re-rehearse
every 90 days. `check-security --rule R82` reads this file.

| Database | Provider | Schedule | Retention | Backups go to | Restore rehearsed on | By |
|---|---|---|---|---|---|---|
| mysql (vent) | self-hosted MySQL on the InterServer VPS, dumped by `deploy/backup.sh` | daily 03:00 server time (cron), freshness check 11:00 | 14 days (`find -mtime +14 -delete` in backup.sh) | `/srv/vent/backups/db-YYYY-MM-DD-HHMM.sql.gz` on the same box, media alongside | 2026-09-30 | Claude, in session |

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

## Still open

The backups sit on the same disk as the database. A failed disk takes both. An off-box copy
(InterServer's Storage VPS line was the plan in `tasks/vps/INTERSERVER-SETUP.md`) needs the CEO to
order the storage; until then this register is honest about where the copies are.
