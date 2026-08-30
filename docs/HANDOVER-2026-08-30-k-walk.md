# 30 August 2026, third session: the K walk finished, and AFC back on

## The K walk: all nine closed

K1-K9 were nine screens with passing tests that had never been watched render.
All nine are now closed in `V-ENT/GATES.md` with evidence, walked on the
evotv_test AVD at 1080x1920 / 420dpi, which is 411 CSS px.

Real state changed, not just pixels:

| Gate | What happened |
|---|---|
| K1 | Sent 5 VC. demo_organizer 255 -> 250, demo_zainab 2400 -> 2405, two completed Transaction rows, reference TXN-95820917 |
| K2 | WithdrawalRequest 6, 10 VC, pending, GTBank. Fee maths right: 2% of N10,000 + N50 |
| K3 | Two real VIP comp tickets at 0 VC, and the VIP tier's `sold` went 0 -> 2 |
| K4 | Invitations tab, plus a real invitation sent to demo_zainab from the form |
| K5 | Invitation banner as demo_ngozi; pressed Yes, invitation 4 -> accepted |
| K6 | Branching picker listing the four gateable questions |
| K7 | All six poll kinds answered against a real ticket and read back out |
| K8 | /terms on a phone |
| K9 | The maintenance page, standalone, counter counting down |

## Three things that cost time on a device

1. **The "Getting there" map swallows swipes.** Use `input keyevent 93`, not
   `input swipe`, to scroll past an event page. A swipe over the map pans it.
2. **`adb shell input text` breaks on spaces.** Use `%s`. Without it only the
   first word lands, silently, and the form looks like it ignored you.
3. **Tapping a tab chip mid-scroll resets the tab.** The console's chips sit
   high, so a swipe starting on one switches tabs.

## Two things that look like bugs and are not

- The invitation banner is **absent for somebody already registered**. That is
  right: accepting an invitation you have already acted on is not a thing to
  offer. K5 needed an invitee who had not registered.
- The branching picker **leaves out the free-text questions**. Also right:
  there is nothing sensible to branch on in a paragraph.

## Found while walking, and fixed

- **The tournament console drew two of everything.** Suppressing the duplicate
  header was half of it; the Actions panel still rendered its whole page shell,
  so on a desktop width two identical sidebars sat side by side and pushed the
  content off screen. Only visible on desktop, which is why the phone walk
  missed it.
- **The tournament hero printed over the card below it at 411px.** A fixed
  280px banner with an absolutely positioned overlay, plus `min-width: 0` on
  the left half letting flexbox squeeze the title to one word per line.

## AFC is back on in production

`AFC_SSO_ENABLED=1` on the VPS, and the button is live again. It had been off
since it created three duplicate accounts this morning. What makes it safe now:

- **No address** -> no account is created. They are sent to sign in normally and
  connect AFC from settings.
- **An address** -> matches an existing account, and anybody previously forked
  onto a placeholder account is healed back onto their real one.

AFC say they are sending an address for approved, consented players. The log
line naming the claims that arrived is how that gets confirmed - watch for
`afc userinfo carried no email. Claims present: [...]` to stop appearing.

**Still open:** a real AFC player signing in end to end (gate Q3) cannot be
proven from here, and the AFC client secret pasted into a chat transcript should
still be rotated.

## The sign-in popup

Fixed after the CEO reported it signing them in and then sitting there. The
whole mechanism had been `window.opener`, which a browser may sever across the
cross-origin round trip to AFC and back. Now neither side depends on it: the
popup knows itself by the name it was opened with, and the opener watches for
the session to appear rather than waiting to be told. Verified by imitating the
broken case - a popup that completes with no message ever sent - and watching
the opener move itself from /login to /home.
