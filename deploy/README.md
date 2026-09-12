# deploy/

Server configuration for the InterServer VPS, kept in the repo so the box can be
rebuilt from scratch without copying snippets out of a document.

| File | Goes to |
|---|---|
| `nginx-vent.conf` | `/etc/nginx/sites-available/vent` (symlink into `sites-enabled/`) |
| `systemd/vent-api.service` | `/etc/systemd/system/vent-api.service` |
| `systemd/vent-web.service` | `/etc/systemd/system/vent-web.service` |
| `backup.sh` | `/srv/vent/deploy/backup.sh`, cron `0 3 * * *` |
| `deploy.sh` | `/srv/vent/deploy/deploy.sh`, run by hand after merging to `main` |

Full build order, sizing and the mail decision: `V-ENT/tasks/vps/INTERSERVER-SETUP.md`.

## The one thing not to get wrong

`/media/` is public and `/private/` is `internal`. KYC identity documents are written
under `PRIVATE_MEDIA_ROOT` (`/srv/vent/private/`) and are only ever released by Django
answering `GET /auth/kyc/document/<id>/` with an `X-Accel-Redirect`, after checking the
caller is the uploader or an admin who may review KYC. If you ever add an
`alias /srv/vent/private/` to a non-internal location, you publish government IDs.

Smoke test after any nginx change:

```bash
curl -o /dev/null -w '%{http_code}\n' https://api.v-ent.co/private/kyc/<any-file>   # want 404
curl -o /dev/null -w '%{http_code}\n' https://api.v-ent.co/media/kyc/<any-file>     # want 404
curl -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <admin>" \
     https://api.v-ent.co/auth/kyc/document/1/                                      # want 200
```

## Two things about the box that are not in git

**`/srv/vent/deploy/deploy.sh` is a symlink to this directory's `deploy.sh`.**
It used to be a COPY, taken on 1 September, and on 10 September that copy quietly
deployed a frontend build and never rolled the instances, because it still
restarted `vent-web`, the single-instance unit retired on 7 September. The site
served the previous build for twenty minutes with every check saying active.
If the box is ever rebuilt, make it a symlink, not a copy.

**`git config core.fileMode false` in `/srv/vent/backend`.** `deploy.sh` runs
`chmod +x deploy/*.sh`, which git sees as a modification, and the next
`git pull --ff-only` then refuses with "local changes would be overwritten by
merge" and aborts the whole deploy.

`deploy/verify-live.sh` exists so neither of these can be silent again: it asks
each instance which build it is serving and fails if it is not the one on disk.
`deploy.sh` runs it after the roll.
