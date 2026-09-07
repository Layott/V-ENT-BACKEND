# Handover, 5 September 2026: what actually happened at the door

Inbox rows 77, 79, 80. Diagnosis only so far. **Nothing is built or deployed for
these yet**, and the reason is at the bottom.

## The number that frames all of it

**1 check-in recorded, out of 1421 tickets, across two event days.** The door
never worked electronically. Everyone else was admitted by eye.

## Yesterday, 4 September, minute by minute

The scanner phone was `102.88.167.193`, on `/events/scan?event=rivalry-series-season-2&gate=Main`.

| Time (WAT) | What the server saw |
|---|---|
| 06:27 - 06:28 | scan page opened from the manage screen; attendee list downloaded, 479353 bytes |
| 06:53 | page reloaded repeatedly. Three list downloads **aborted by the client (499)**, then bodies of **245202** and **310738** bytes against a full list of **480171** |
| 06:55:05 | `POST /event/ticket/VT-NWKL9Z2K/check-in/` -> 200. Ginnie. **The only successful check-in of the entire event** |
| 06:56:48 | **the last request that phone made all day** |
| 10:00 | doors open |
| 11:20 | the build carrying the row 67 fix goes live |

The phone stopped talking to the server four minutes after its single check-in
and never resumed. `/events/scan` was loaded 24 times in half an hour, which is
not somebody scanning, it is somebody reloading a page that is not working.

**The row 67 fix could not have helped.** It deployed at 11:20, and a deploy does
not reload a page already open. That phone had held its bundle since 06:53, so it
ran the OLD scanner - the one with no server fallback - for the whole day. See
[[project_deploy_breaks_open_pages]].

## The person who was told they were not registered

`VT-CXENEJ3Q`, which the CEO reported. Checked in the database:

```
event 13 RIVALRY SERIES SEASON 2   status valid
tier General Admission Day 1 (2026-09-04)
Riley, samierasaq@gmail.com, guest checkout, no account
bought 2026-09-04 05:26:56 UTC
checked_in_at = None        <- never got in
```

The ticket is real, valid, for the right day, and **it is present in the
attendees payload today**. So this was never a bad ticket.

What refused it was the device: an old bundle with no server fallback, holding a
list that was either downloaded before 05:26 or truncated by the aborted
downloads at 06:53. Which of those two, I cannot prove without the phone. Either
way the code was absent from the copy on that device and the old bundle had no
way to ask the server, so it said "not on the list".

## Today, 5 September, a different surface

Nobody opened `/events/scan` at all. The door phone (`102.88.115.165`) is on
`/events/<slug>/attendees`, which **has no scanner**. Staff scan the QR with the
phone's camera app, read the code, and type it in.

**Two POSTs from that phone all day, both `/auth/login/`.** Zero check-in calls,
not even a CORS preflight. So they are typing into the **Search** box, not into
"Check someone in". Search filters a list fetched once at page load
(`useEffect(load, [load])`, deps token and eventId only, no refetch), and answers
**"Nobody matches that search."** That sentence is the "not found", written in
the browser, with no request leaving the phone.

The list grew 622034 -> 648322 bytes across the day as people kept registering,
which is precisely the CEO's own second observation.

## Self check-in: mostly built, and completely unreachable

`vent_event/views_self_check_in.py` is 194 lines and it is good work: a window
with opens/closes, a row lock so two taps cannot double-admit, and a second
factor that already handles guests - a signed-in owner passes on ownership, and
anybody else must supply the email on the ticket. That matters here because
**1336 of the 1421 tickets are guest checkout**, so an account-only design would
have covered 6 per cent of the room.

It also already records how somebody got in: `checked_in_gate = SELF_GATE`
(`'self'`), and `checked_in_gate` is already in the attendees payload. So
"organisers can see who checked themselves in" is nearly free.

**What blocks it:**

1. `Event.self_check_in` is **False** on event 13, and
2. `self_check_in_settings` is **`@api_view(['GET'])`** - read only. There is no
   write endpoint and no screen, so **no organiser can turn it on by any route**.
   This is inbox row 47's "an organiser cannot configure self check-in",
   confirmed as a real block rather than a tidy-up.

## What needs building

| | |
|---|---|
| A ticket lookup that **does not admit anybody** | today the only way to ask the server about a code is `check-in/`, which checks them in. Search cannot use it |
| Search **falls through to the server** on a local miss | the same shape the scanner already has |
| The attendees list **refreshes itself** during a door | with backoff and a `since` delta. 648KB per poll per phone will fail `check-pollers.mjs`, and rightly |
| A **link to the scanner** from the attendees page | staff plainly cannot find `/events/scan` |
| Self check-in: **a write endpoint and an organiser screen** | on/off plus the window |
| Self check-in: **the attendee list distinguishes and filters** self from door | `checked_in_gate == 'self'` already carries it |
| The organiser door **stays exactly as it is** | the CEO was explicit: self check-in is additional, not a replacement |

## Why nothing is deployed

A deploy replaces the stylesheet and bundle under every page already open, and
the event runs until 23:00 UTC today. Pushing now breaks the door phone
mid-check-in. Waiting on the CEO's call.

**Workaround needing no deploy:** type the code into **"Check someone in"**, not
into Search. That field always asks the server and works for a ticket bought a
minute ago. Verified against production: an already-used code answers 409 with
who and when, an unknown code answers 404, and a valid one admits.

---

## Addendum, 6 September: can the searched names be recovered?

The CEO asked whether the names or emails typed into the door list between
Friday and Saturday can be seen. **They cannot, and nothing was lost: they were
never recorded, by design.**

Four confirmations, taken independently rather than from memory:

1. `src/app/events/attendees/page.js:104-109` - the filter is a `useMemo` over
   `rows` already held in the browser. Typing calls `setSearch` and nothing else.
2. `/events/scan` behaves the same. Its only calls out are the attendee list GET
   (line 162) and the check-in POST (271, 337).
3. Nginx for 4 and 5 September carries no request with a door search term. Every
   `q=` or `search=` hit is something else: `/_next/image?q=` is image quality,
   `/auth/search-users/?q=` is the site-wide people search, plus `/events?q=`
   and `/tournament/search-tournament/?q=`.
4. Gunicorn logs the request line only. No bodies, and there would be no body.

### The one number, confirmed against the database today

```
Ticket.objects.filter(event_id=13)         1422
  checked_in_at is not null                   1
  VT-NWKL9Z2K | Ginnie | regina.e.okoko@gmail.com
              | 2026-09-04 05:55:05 UTC | gate Main
```

### Correction to the body of this handover

Above it says Saturday saw zero check-in calls. Two POSTs do exist, at 17:45:16
and 17:45:17: `VT-NWKL9Z2K` answering 409 and `VT-ZZZZZZZZ` answering 404. Those
were this project's own production verification, one second apart, and not the
door. The claim that matters is unchanged: **no member of staff made a check-in
call on Saturday.**

Separately, the `GET /tournament/<n>/check-in/status/` lines in the same logs are
the tournament check-in feature, a different surface from the event door, and
should not be counted as door traffic.

### What this asks of the row 77 fix

The server-side lookup is what would have made the question answerable. Once
Search falls through to the server on a local miss, each lookup becomes a log
line carrying a code or a name, and "who did the door try to admit" becomes a
query rather than a dead end. Build the recording deliberately rather than
letting it be a side effect, and decide the retention period at the same time,
because these are attendees' names and email addresses.
