# Handover, 31 August 2026: clubs as group chats, and organisations that work

Written while the work is in flight. If the session ended now, this plus
`V-ENT/GATES.md` is enough to carry on.

The CEO's batch of ten, from 31 August. Items 1 to 4 were closed on 30/31
August (event address, notification drawer at the top, Coming Soon badges,
nested hamburger). This file covers items 5 to 9.

---

## Done and proven

### Clubs are group chats now (AA9 to AA13, backend done, frontend written)

**What was there:** a club was a name, a member list, and a feed of posts.
Nothing to say anything in.

**What it is now.** Three models in `vent_auth/models.py`:

- `ClubMember` gained `role` (owner / admin / moderator / member), `scopes` of
  authority through `RANK`, `muted_until`, and `outranks()`. Every "may this
  person act on that person" question goes through `outranks`, in one place.
  Two endpoints deciding it separately is how a moderator ends up able to
  remove an owner.
- `ClubTopic` - a club holds named topics, and every message belongs to one.
  The CEO asked for messages "around particular set topics".
- `ClubMessage` - deleting is soft. A moderator removing a message should not
  also remove the evidence of what was moderated.

Migrations `0063` (auto) and `0064` (hand-written: every existing club gets an
owner row and a "General" topic) are applied.

Endpoints in `vent_auth/views_clubs.py` (helpers) and
`vent_auth/views_clubs_actions.py` (the endpoints), routed in
`urls_settings.py` under `club/<str:club_ref>/...`:

```
GET  /club/<ref>/overview/                 club, topics, and what the caller may do
GET  /club/<ref>/members/                  members with roles
GET  /club/<ref>/topic/<id>/?after=<id>    messages; `after` returns only the new
POST /club/<ref>/topic/<id>/post/          say something
POST /club/<ref>/topic/create|<id>/update|<id>/delete/
POST /club/<ref>/message/<id>/delete/      soft delete
POST /club/<ref>/role/                     appoint or demote
POST /club/<ref>/remove-member/  /mute/  /leave/
```

**Verified:** `vent_auth/tests_clubs.py`, 24 tests, OK. The ones worth knowing
about, because they are the rules an organiser will lean on:

- a moderator cannot delete an admin's message, and equal rank cannot demote;
- only the owner may make somebody an admin (an admin who can make admins can
  hand the club away);
- a muted member still reads, and the mute expires by itself because
  `muted_until` is a time, not a flag;
- a removed message keeps its place in the thread and loses its words;
- the last topic cannot be deleted, because a club with no topic has nowhere to
  say anything.

**Also fixed on the way:** `club_create` made its creator an ordinary *member*
and created no topic. Both fixed. `club_join` took an `<int:club_id>` and now
takes the slug like everything else.

**Frontend:** `src/app/community/club/page.js` rewritten as a chat: a topic
rail, a thread that polls with `after` every 8s, a composer that explains
itself when you may not use it, a members tab with role and mute controls, and
an about tab. 45 new keys in en, fr and pt. `dict-parity` and `check-keys` both
clean. **Not yet walked in Chrome or on the emulator.**

### Organisations (AA14 to AA17, backend done)

**AA14, the logos.** Found it. The create wizard held only
`URL.createObjectURL(file)` and posted that string as `logo` inside a **JSON**
body. `request.FILES` was therefore empty and `org_create` dropped the picture
without a word. Same shape as the tournament wizard fault. Fixed both ends:
the wizard now holds the `File` and posts `FormData`, and there is finally an
endpoint that can change a logo afterwards, which the wizard's own copy ("you
can update them anytime") had been promising with nothing behind it.

**AA15/AA16, invites and roles.** New `OrgInvite` model, carrying an opaque
token (`oi_<20 hex>`) rather than its primary key: an invitation identifier
that can be guessed by counting is an invitation anybody can accept. An invite
names the role **and the scopes** up front, so accepting is one press rather
than a request somebody then has to grade.

`OrgMember` gained `scopes`, a `RANK` ladder, `outranks()`, `areas` and
`may_run(area)`. Owner and admin hold every area; a **manager** holds only what
is stored on their row, from `teams / events / tournaments / clubs`. That is
the CEO's "different roles to manage different things", stored rather than
remembered.

`org_promote` used to be owner-only with no ladder, and `org_kick` let an admin
remove another admin. Both now go through `outranks`.

**AA17, what an organisation holds.** `Club.organization` added.
`Event.organization` already existed - `org_events` was answering an empty list
with a comment saying events were not org-owned, weeks after the foreign key
landed. Fixed, and `events_hosted` on the card is a real count now.

New endpoints in `vent_auth/views_orgs_manage.py`:

```
POST /organization/<ref>/update/                profile + logo + banner (admin+)
GET  /organization/<ref>/capabilities/          what the caller may do
POST /organization/<ref>/role/                  role + scopes
POST /organization/<ref>/invite/                invite by username with a role
GET  /organization/<ref>/invites/
POST /organization/<ref>/invite/<token>/cancel/
GET  /organization/invites/mine/
POST /organization/invite/<token>/respond/      {accept: true|false}
GET  /organization/<ref>/clubs/  POST link-club/ unlink-club/
```

**Verified:** `vent_auth/tests_org_manage.py`, 24 tests, OK. Including the two
that pin the actual bug: a logo sent as a file is stored and comes back as a
URL, and a `blob:` URL sent in JSON is **not** mistaken for a picture.

Migration `0065_club_organization_orgmember_scopes_orginvite` applied.

Full backend suite before the org work: **1609 tests, OK**.

---

## Still open

| | |
|---|---|
| AA2 | the map fix is in the code, never looked at in a browser |
| AA5 | drawer behaviour on desktop, not verified |
| AA13 | the club page in Chrome and on the emulator |
| AA15/16/17 frontend | the manage page still toasts "Invite flow coming soon."; no scope controls; no clubs tab; no screen showing an invite you have received |
| AA18/AA19 | My tickets said no ticket while holding one; counters must be real |
| AA20 to AA24 | full suite after everything, lint, emulator walk, desktop walk |

Longer-standing, from before this batch: the reminder cron is still not
installed on the VPS, Q3 (a real AFC player sign-in) is unproven, and the AFC
client secret still wants rotating.

---

## Traps hit today, so nobody hits them twice

- **`bash <<'EOF'` truncates.** Writing a large Python or JS file through a
  heredoc failed with "unexpected EOF while looking for matching `''" three
  times. Write the script to a file with the Write tool and run it.
- **A key built by interpolation is invisible to `check-keys.mjs`** and
  therefore silently English forever. The club page had
  ``tt(`ui.club.role.${role}.a1`, role)``; it is four literal keys now.
- Setting `Content-Type` by hand on a `FormData` post drops the boundary and
  the body arrives unparseable. Leave it to the browser.
- Multipart flattens everything to a string, so an array field arrives as its
  JSON text. `org_create` was storing that string where the page expects a
  list. `_parse_links` handles both.
