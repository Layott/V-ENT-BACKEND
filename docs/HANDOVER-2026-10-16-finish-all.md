# Handover, 16 October 2026: finish everything left (inbox 456 to 461)

Picks up the 13 Oct pause (docs/HANDOVER-2026-10-13-bracket-editing.md, section PAUSED).
Gates: V-ENT/gates/66-everything-left-13-oct.md. Inbox rows 456 to 461 are this session's asks.

## Asks taken 16 Oct (tasks/inbox.md)
- 456 "/goal finish up all that is left, end to end."
- 457 permission for nginx http2 and moving vent.pre-bluegreen out ("i give you permision").
- 458 leave the Safari question (438) and the Rivalry names (69). Parked, not worked.
- 459 "for the 431 what is it?" Answer: the admin dashboard CMS (/admin/records) is built and live since
  10 Oct; its one open check is a signed-in admin walk on production, which needs an admin account and
  its authenticator code typed into the browser by the CEO, after which the walk is read only.
- 460 which sitemap to submit in Search Console and what else to do (screenshot: sitemap.xml and
  sitemaps.xml "Couldn't fetch", submitted 2 Dec 2025).
- 461 "i dont ever want those issues happeniong, so let's create automatic checks for them".

## DONE and LIVE
- F2 http2 (457): the live file differed from deploy/nginx-vent.conf only by http2 on the four 443
  listeners. Backup /etc/nginx/sites-available/vent.bak-2026-10-16-before-http2;
  vent.pre-bluegreen moved to /etc/nginx/sites-disabled/; nginx -t clean with 0 conflicting-name
  lines (was 6); ALPN h2 on v-ent.co and api.v-ent.co; Chrome nextHopProtocol "h2". The file in the
  repo still rides on feature/bracket-editing (records worktree).
- 455 invites to people with no account, I1 signup name check, J1 IDOR cases: BE #241 FE #249,
  build 3l9JWbMXXr6WTt--7LT4J, migration vent_auth.0092 applied, verify-live green.
  Walk: V-ENT/tasks/audit/invite-anyone-walk-2026-10-16.md. Faults found and fixed on the walk:
  withdraw said nothing (and the full reload unmounted the block), the org Invitations tab hid that
  an email works and drew "?" for an emailed invite, "Invites" untranslated, the event console
  handed a refused stranger the door scanner, door staff "Open" went to that refusal, a 20px link.
  Signup: asked get-username-with-email (400 without an email) so every name read taken, and the
  message cleared itself so a taken name passed the gate; now check-username-availability, newest
  keystroke wins, the message holds, "free" said, en/fr/pt, number rule shown.
  Tests 4129 OK; --idor 0 high 0 medium.

## Wrong turns and traps
- 4 IDOR "highs" were fixture drift: walk_buyer_ada (user B) had been given ticket VT-3ZXP49JA by
  tasks/session-tools/games-walk.mjs. Cancelled locally (sqlite backed up in the session scratchpad).
  A walker must never give B anything on walk-con-5e6c.
- Patching .mjs regexes through a Python heredoc doubled and dropped backslashes (R41 again). Edit the
  file directly.
- The emulator's first load after split-dictionaries rewrote files under the dev server showed
  "Cannot read properties of undefined (reading 'call')"; a reload cleared it (dev only).

## 460 / 461 search, DONE and LIVE (BE #242 #243, FE #250 #251)
- Sitemap: /users/indexable/ (active, not deactivated, visible to a visitor, "show in search" on;
  usernames and a date) feeds /u/<username>; an event with stalls gets /events/<slug>/vendor-shop;
  anime series and battles list once the module opens; /partners removed (it serves noindex).
- next.config htmlLimitedBots: Next streamed title/canonical 37 KB into the body for Googlebot; now
  in the head for Googlebot and the AI crawlers. /sitemaps.xml and /sitemap_index.xml forward (308).
- noindex on the old ?id= addresses and /search (they told Google "index me", self-canonical).
- check-seo fixed (shape matching, both noindex spellings, "listed but noindex"), blocking in check-all.
- check-search.mjs (20/20 self-test) reads production like a crawler; deploy/search-check.sh runs it,
  deploy/verify-live.sh fails the deploy on it, cron 0 6 * * * mails BACKUP_ALERT_EMAIL on a fault.
- Its first run after deploy FAILED the verify and found two real faults, both fixed in BE #243:
  (1) the box rate-limited its own server renders (one client to nginx; 170 of the recent 429s were
  the box; pages went out noindex past 120 renders a minute). deploy/nginx-limits.conf is now the
  tracked copy of /etc/nginx/conf.d/vent-limits.conf; api and auth zones keyed on a map empty for the
  box. nginx RELOAD refused the zone-key change while systemd said Reloaded: a restart was needed.
  Proven: box 220 x 200, outside 429 after 68. (2) "keiji_is_lost " (trailing space, saved by
  edit-profile-info as typed) had a profile that never opened: the door now applies the username
  rules (USERNAME_INVALID at both rename doors), migration 0093 stripped it.
- Signup's limit is now 20 like the server (it said 30).
- Production 16 Oct 14:26: "666 sitemap address(es) checked on https://v-ent.co, 0 problem(s)".

## STILL OPEN after that
450/452 screens (server UNCOMMITTED on records feature/bracket-editing), 453 toornament research,
C1 event ticket tab error state, E1 EAFC F5 production walk. 431 waits on the CEO's code.
