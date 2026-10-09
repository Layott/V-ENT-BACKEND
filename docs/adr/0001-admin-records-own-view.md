# 0001. The console builds its own records view rather than using Django's admin

Date: 9 October 2026. Status: accepted. Inbox 420.

## The ask

CEO, 8 October 2026: "Admin dashboard: Let it work as a proper CMS for the entire site, if we add a
new feature let it automatically build the control on the admin dashboard, so everything on the
website can be edited, removed, deleted, or even restored, should be able to check all info for
specific". Decided the same day: every model appears automatically; secret and sensitive columns
never leave the server; money records are view only and a correction is a new entry with a reason;
delete goes to a bin for 90 days and can be restored, purge only for a super admin; every edit is kept
and can be reverted; a new "Edit records" permission.

## The obvious answer, and why it is not this one

Django ships an admin that lists every registered model and edits any row. On paper it is most of
the ask. It is not used, for five reasons, each of them a fact about this platform:

1. **It is switched off in production, on purpose** (inbox 400, `tests_django_admin_off.py`).
   Uploaded overlay HTML runs its script on the API's own origin by design, so a staff member signed
   in to Django's admin there lends that script their session. Turning it back on reopens that.
2. **It has a second sign-in.** Django's admin authenticates with Django sessions and `is_staff`.
   The console signs in once, with the site token and the admin's two-factor code
   (`project_one_door_2fa`), and every permission is a V-ENT role from `decorators.ROLE_PERMISSIONS`.
   Two doors means two sets of rules that drift, which this codebase has already paid for.
3. **Its delete destroys the row.** The ask is a bin that restores. Django's delete has no bin, and
   a restore cannot be bolted on from outside it.
4. **It edits money like anything else.** A wallet balance would be a number field. Here money is
   view only and corrected by a new entry through `wallets.transfer`, so the statement still says
   what happened.
5. **It shows every column.** Password hashes, session tokens, PIN hashes, two-factor secrets and
   bank account numbers would all be on screen. Here they never leave the server.

Each could be patched in Django's admin with ModelAdmin overrides per model, which is exactly the
per-model work the CEO asked to be rid of ("automatically build the control"), on a surface that is
off in production.

## What was built instead

`vent_auth/records.py` reads every model Django knows (211 V-ENT models on 9 October), excluding
Django's and the sign-in libraries' own tables by name and with a reason. The console's
`/admin/records` draws any of them from the field definitions, so a model added next month appears
the day it is migrated with no code. What a new model may need is a mark, money or sensitive, and
`tests_records.py` fails until it has one:

- a model with a money-looking column must be in MONEY or NOT_MONEY;
- a secret-looking column must be in SENSITIVE or NOT_SENSITIVE;
- a mark naming a model or column that no longer exists fails too.

Delete collects everything that cascades (Django's own collector), refuses when that reaches money
or something protected, or when a tournament or event has paid entrants (the organiser's own rule),
and writes all of it to `RecordBin` before deleting, in one transaction. Restore inserts the rows
back with their own numbers and refuses rather than overwrite anything that took their place.
Tournaments and events keep their existing soft delete. Every change is a `RecordVersion` with the
before and after of each column, and a revert is a new version naming the one it undoes. Every
action writes an `AdminAction`.

## What this costs

Model and column names are shown as the database names them (English identifiers), like codes,
rather than translated into French and Portuguese: there are thousands, they change with every
migration, and an admin reading a raw record is reading the database. The console around them is
translated. Editing covers plain columns and links by number; files and pictures stay on the screens
built for them, because uploads go through the uploads rules.
