# Local Database — V-ENT Backend

How the local MySQL for this backend is set up on this machine, and how to run against it.
Created 2026-07-06 during the M1 integration pass.

---

## TL;DR

- MySQL runs in a dedicated Docker container **`vent_mysql`** (image `mysql:8.0`) on host port **3307**.
- `.env` is set to `DB_PORT=3307` (everything else unchanged: `DB_NAME=vent`, `DB_USER=admin`).
- Run Django with the **`venv`** interpreter, NOT `vent`:
  `venv\Scripts\python.exe manage.py migrate` (or `runserver`).

---

## The container

```
name:     vent_mysql
image:    mysql:8.0
ports:    0.0.0.0:3307 -> 3306   (host 3307 maps to MySQL's 3306 inside the container)
restart:  unless-stopped         (survives reboots)
db:       vent          (auto-created by the image via MYSQL_DATABASE)
user:     admin         (auto-created via MYSQL_USER; password = the .env DB_PASSWORD, unchanged)
```

Start / stop / status:
```
docker start vent_mysql
docker stop  vent_mysql
docker ps --filter name=vent_mysql
```

### Root password
Generated randomly at container creation and stored **only in the container environment**.
Retrieve it when needed:
```
docker inspect vent_mysql --format "{{range .Config.Env}}{{println .}}{{end}}"
```
Look for `MYSQL_ROOT_PASSWORD`. (The `admin` user is what Django uses; root is rarely needed.)

---

## Why port 3307 and not 3306

Host port **3306 is already occupied** by an **unrelated project's** container, `afc_mysql`
(`mysql:8.0`, database `afc_db`). That is why a straight connection on 3306 returns
`1045 Access denied for user 'admin'` — the V-ENT `admin`/`vent` credentials do not exist in
that other container. **Do not touch `afc_mysql`.** V-ENT runs isolated on 3307.

There is also a native `MySQL Server 8.4` installed under `C:\Program Files\MySQL\` but it has
**no Windows service and is not running**; it is not used here.

---

## Which Python / venv

- ✅ **`venv\Scripts\python.exe`** — the working interpreter (Django 5.0.7, `pymysql` installed).
- ❌ **`vent\Scripts\python.exe`** — BROKEN. Its `pyvenv.cfg` points at another machine's Python
  (`C:\Users\hp\AppData\Local\Programs\Python\Python39-32\python.exe`). Do not use it.

Typical commands (run from `V-ENT-BACKEND\`):
```
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py runserver
venv\Scripts\python.exe manage.py check
```

---

## Resetting local data (wipe + re-migrate)

Local dev data is disposable. To start clean (e.g. after a migration change):
```
docker exec vent_mysql mysql -uroot -p"<root-from-docker-inspect>" -e "DROP DATABASE IF EXISTS vent; CREATE DATABASE vent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
venv\Scripts\python.exe manage.py migrate
```
Or recreate the container entirely:
```
docker rm -f vent_mysql
docker run -d --name vent_mysql --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD=<new> -e MYSQL_DATABASE=vent \
  -e MYSQL_USER=admin -e MYSQL_PASSWORD=<the .env DB_PASSWORD> \
  -p 3307:3306 mysql:8.0
```
(Set the app password from `.env` DB_PASSWORD — keep it identical so `.env` still authenticates.)
