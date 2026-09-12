# Handover, 12 September 2026: everything left, and what the honest checker found

CEO, via `/goal`: "finish up eevrything left to be done". Gates in
`V-ENT/gates/32-everything-left-12-sept.md`; inbox row 257. A second session
("backend pr review", the CEO's other window) took item 10 (ops) and half of
item 9 by agreement; its rows are 258 and up and its gates file is
`gates/33-item-10-ops.md`.

Read `HANDOVER-2026-09-10-READ-FIRST-state-of-the-platform.md` first. This file
is what moved since.

---

## 1. What "left" turned out to be, measured

| Thought to be open | Actually |
|---|---|
| Two branches pushed with no PR (BE docs/read-first, FE distdir checker) | Already merged on 10 September as BE#177 and FE#186. The READ-FIRST file was written from a local main that was behind origin |
| Inbox row 96, "every page updates on its own", marked NOT done | Done on 7 September (FE 135d69e). Nobody came back to the row. Row updated |
| BE#150 and FE#171, CONFLICTING since 4 September | Merged with main, green, MERGEABLE, pushed (below) |
| Standing debt `spinner for ever`, 42 files | 21 of the 42 were not the fault; checker calibrated; the rest fixed (mine) or in progress (peer) |
| One stray `EVIDENCE: pending` in GATES-TICKETING H4 | A paste slip under a checked box; repaired |

Everything else open waits on the CEO (premium price, rows 26/28, overlay files,
the ten names, row 139) or on AFC's endpoints, exactly as the READ-FIRST says.

## 2. BE#150 and FE#171: the merge, and what the honest checker found

**Branches:** BE `feature/eafc-squads-and-catchers` at 2e6f0a8b, FE
`feature/eafc-squads-ui` at 6dbd756. Both MERGEABLE on GitHub. Merge is the
CEO's call.

**What main already had.** The Sits control (04041a97) and the organiser's
SquadRulesPanel + SubmittedLineups (7 September). So FE#171's SquadReviewPanel
was retired and its decision buttons moved INTO SubmittedLineups; the branch's
studio files were replaced by main's (the auto-merge had produced three copies
of `numberFrom`). What survives from the branch: the player's submit
(LineupPicker + SquadStatus), the font upload in StudioMedia (now registering
each uploaded face under its bare slot name so the "Aa" preview is real), and
the `lineupEpoch` wiring.

**Twenty catchers were written on 4 September and never wired into check-all**
(the block sat on this PR for eight days). Wired now. First run against today's
main found:

- **error copy: 25** places showing the server's English sentence to a person.
  All through `apiMessage` now; `window.prompt` on the stall page is an inline
  field.
- **null images: 2** reader images with no guard.
- **language blocks: 1**, a false positive (`{h} h {m} min` is the same in
  French and Portuguese). The checker knows a units-only string now.
- **guides: 38** screens with no walkthrough. NEW VISIBLE DEBT, recorded in the
  ledger, not started. It is the biggest number in the table.
- **tab strips: 1** (`.hourBars`), fixed.

**The per-file endpoint-caller rule found 14 endpoints with no screen.** Eleven
were features built, tested and unreachable by a person. Every one has a door on
FE#171 now:

| Endpoint | Door |
|---|---|
| anime battles approve | "Run this battle": open voting, close, take nominations again, let a name in, take a character out. Backend now sends `pending` to whoever `may_run` |
| anime room analytics | a host returning to a closed room sees the figures |
| billing subscription change | "Move to another plan" on /memberships, date said before the press |
| billing plan earnings | Earnings tab beside Members and Payments on the organiser panel |
| discord server log | "What the bot did" on the server panel |
| marketplace admin holds | section on admin finance; says "closed" while Vermillion City is shut |
| wallet card default | Make default calls the endpoint (it wrote the settings blob and reverted on reload) |
| vendors create | "Add a stall" by email or @username on the Vendor pitches tab; an unclaimed address becomes a VendorInvite. Backend: `_actor_for_event` instead of creator-only, `invitee_for`, 6 tests |
| ticket lookup | "Look up only" beside Check on the door |
| head to head | two pickers and Compare under the standings |
| discord guild callback, cards/formations | DELIBERATE with reasons |

**Walked in Chrome (desktop, and 390 via iframe):** submit a squad, "1 waiting
for you", Send back refused without a reason, sent back with one, resubmitted,
Accepted, the player's picker following each state; Add a stall by email
(201, "Invited. The stall opens when ... joins V-ENT"); Look up only ("Real,
and good for today", 0 in stays 0); head to head (RS Nigeria 1 won ... against
RS Ghana, matching the table). No console errors. NOT walked: the admin
finance holds section and the battle console, both behind sign-ins the browser
tool may not perform (a password is never typed by this tool); the CEO can
open /admin/finance and /anime/battles/fastest-in-a-straight-line signed in.

**Suite:** `Ran 4047 tests, OK (skipped=1)`. **Build:** 153 routes, after the
pnpm store had to be repaired again (`processChild.js` missing; the recipe in
memory works).

## 3. The spinner debt, and what the checker did not know

`check-spinner-forever` reported 42. Read one by one, 21 were not the fault:
button spinners (login, signup, the password pages, four profile-edit
panels), Suspense fallbacks (seven pages where the style object pushed
`fallback=` ten lines above the text), a computed boolean, and errors drawn
mid-chain or under a name other than `error`. The checker knows all of those
now (15 self-test cases), and `\bLoading[.…]` is case-sensitive again because
it had been matching the dictionary key `ui.loading.challenge`.

Fixed on `fix/spinner-for-ever` (off the FE#171 branch, at 5af6e3a):

- admin user page: a network failure rendered as "User not found"
- UserProfileGallery: `fetchError` was set on every failure and drawn on none;
  the block was commented out
- SecurityPanel: a failed load said "No sign-ins recorded yet"
- UserPicker: a failed search said "Nobody on V-ENT matches that"
- FixtureDetail: a failed tie load said "One match, and that is the result"
- EventSchedule: a failed load emptied the section

And one deletion: `UserProfileOverviewRight` and three children were imported by
nothing. UserProfileStats was on the list and fixed before I opened the page it
was supposed to be on and found no page had it. Deleted.

Remaining 7 (all under src/app) are with the peer session on its own branch off
`fix/spinner-for-ever`: community/post, events, events/vendor-shop,
organizations/manage, partners, tournaments/register-tournament, wallets. Gate
D2 closes when the union reads 0.

## 4. Three catcher faults found by using the catchers

1. **check-stale-gates waved through any expectation of five or more words** on
   exit 0, and `tail -1` had already turned exit 1 into 0, so `0 that can spin
   for ever` passed against "41 that can spin for ever". Then, matched
   literally, `0 that can spin for ever` was INSIDE "20 that can spin for
   ever". Backticked expectations are literal now, and a number is a whole
   number. Two fixtures, six cases. **Write EXPECT values in backticks.**
2. **check-keys never saw the third argument of `apiMessage`**, so four keys
   used at real refusals did not exist in any language. It reads that shape
   now; 418 more keys checked.
3. **endpoint-callers printed a sentence as its last line**, and the ledger had
   recorded "the reason it is not meant to be called." as its count. The count
   is last now.

## 5. Two things that cost time, for the next person

- `\b` written through a Python heredoc arrives as byte 0x08 in the file (three
  times today). `check-control-bytes` catches it; the fix is
  `s.replace(chr(8), chr(92)+'b')`. Write regexes to a file with `cat <<'EOF'`
  or patch by line number.
- The Chrome tool will not type a password and must not; a walk that needs an
  account uses whatever session the browser already holds. Signing OUT of the
  browser's session (I did, to switch accounts) cannot be undone from here.

## 6. Open at the end of this file

- Gate D2/D3 (spinner 0): peer's seven files
- Gate D4 third page: one of the peer's pages, walked once they land
- Gates C3/D4 admin and anime walks: need the CEO signed in
- 38 guides: new debt, named, not started
- Two PRs to merge (BE#150, FE#171), then `fix/spinner-for-ever` and the peer's
  branch on top
