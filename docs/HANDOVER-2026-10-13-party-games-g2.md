# Handover, 13 October 2026: party games G2 (the vote, games in checkout)

Picks up from docs/HANDOVER-2026-10-12-inbox-434-439.md (last section: G2 half built when the
PC shut down). Order of the remaining work, unchanged: G2, G3 fees (449), G4 registration draft
with the ticket step (436), then inbox 450 (edit started tournaments, format per stage).

Branch feature/party-games-vote in BOTH V-ENT-BACKEND-records and V-ENT-FRONTEND.

## G2, what was built today

Server (V-ENT-BACKEND-records):
- views_games.games_vote / games_vote_run (from 12 Oct) plus three fixes found walking:
  1. the count hid from somebody holding two tickets who voted with the second (visibility was
     judged on the first ticket); `_vote_payload` now judges it on a ticket that has voted.
  2. a catalogue key already on the event (Jenga) made a second, proposed copy; it now reuses
     the event's game.
  3. PATCH can no longer set `proposed` by hand (only the vote door does), so a game cannot
     leave the public list with no vote to bring it back.
- Games ticked in checkout travel WITH the purchase and are joined by the server when the
  ticket is issued: `game_entries.read_picks` (read before money moves, 400 on a bad list,
  at most 20) and `join_picks` (skips a game that closed or came off, never raises) and
  `ticket_games` (rows named the way serialize_game names a game: catalogue_key or
  custom.name). Wired at every door that issues tickets: buy-ticket (wallet), guest-buy free,
  Paystack metadata `games` (joined in guest-verify), Flutterwave order `games` (joined in
  fulfil_flutterwave, so the webhook path counts too). Answers carry `games`.
  `_ticket_row` (guest) carries `event_slug` so the confirmation page can link to Games.
- Tests: vent_event.tests_party_games 41 OK (CheckoutPicksTests: wallet buy, full game to the
  waiting list, another event's game never joined, bad list refused before buying, free guest,
  Paystack round trip joined once on a double return, own game named; vote fixes).
- security/idor-cases.json +2 (open a vote, run a vote, as B).

Frontend (V-ENT-FRONTEND):
- components/party-games/GamesVoteConsole.js: "Let people vote" in the console Games tab
  (ticks of the event's games, add one from the catalogue, question, pick up to, closes;
  when open: ranked count, how many to run, Run the top N, Close the vote). A blank question
  is sent in the organiser's language (it went in English on a French event).
- GamesVote.js (from 12 Oct) on the event Games tab; "In the vote" tags in the console list and
  catalogue.
- components/party-games/CheckoutGames.js: the optional games step (buy modal step 1 and
  GuestCheckout), picks kept in sessionStorage `vent-game-picks:<event>` across a card trip,
  `GamesJoinedNote` on the three success screens (modal, guest form, /events/ticket-confirmed),
  with a link to the Games tab.
- en/fr/pt by hand: 48 games.vote.* / api.GAME_VOTE_* keys, 6 games.checkout.*, and
  buy.successLine (the modal's "1 × Free entry for ..." was English on every page).
- event-games.module.css fallback class `ownTile` did not exist (check-css-classes found it);
  now ownThumb.

## Local fixtures changed today (local sqlite only)

- walk-con-5e6c moved 26 days forward (start 2 Nov 2026, tier days 2 and 3 Nov) so its buy
  doors open again (it ended 7 Oct).
- New local tickets: walk_buyer_ada Free entry (games Whot, Ludo waiting), guests
  guest.g2@example.com VT-WKFBAJPX, guest.g2b@example.com VT-TTNGEJDT.
- Local backend runs with EMAIL_BACKEND console: the Gmail relay times out locally and every
  buy then takes 20 seconds.

## Wrong turns, recorded

- A Bash heredoc holding Python with French apostrophes failed to parse twice; scripts now go
  to files (scratchpad patcher.py with exact anchors, keeps CRLF).
- Claude Chrome's window kept being minimised (the owner's desktop); chrome-window.ps1 with a
  marker title found nothing because a minimised window keeps the title "Google Chrome";
  `-mode front -match "^Google Chrome$"` (exactly one) restored it.
- One press landed while the extension reported "not connected": the server ran it, the page
  showed the old state until reload. Not a product fault; re-pressed with the page healthy
  and it updated at once.

## G2 DEPLOYED (13 October, 13:27)

BE #237, FE #245 merged; backup /srv/vent/backups/db-2026-10-13-1327.sql.gz; no migration; both
ports on build YZwlIVpWdqFfEUbmvgG3P. Live read: anime-night-lagos games 200 with `vote` null,
the vote and run doors 401 to nobody, the French Jeux tab renders with no console error. Walk
log V-ENT/tasks/audit/party-games-g2-walk-2026-10-13.md, gates/65 G2 3/3.

Seen, not G2 (left): the R88 mediums for the stall pictures (4 addresses) and the admin record
bin (6) are the ones named on 12 October; the security ledger reads them as "rose 0 -> 2"
because it had recorded 0, not because anything new appeared.

Next: G3 entry fees on games (inbox 449).

## G3: entry fees on games (inbox 449), 13 October, afternoon

Branch feature/party-games-fees in both repos (cut from main after #237/#245). Decisions, made
with the build and recorded here (silence = agree, as the CEO set on 8 October):
- the fee is whole coins on the game (`EventGame.fee_vc`, 0 to 1000), an event sale like a
  ticket: the same platform fee as a ticket, on whoever the organiser put the event's fee on,
  written to the event's ledger keyed to the entry (`EventLedgerEntry.game_entry`, new);
- paid with the PIN from whoever is signed in (a guest ticket signs in to pay; the coins go back
  to that account, `EventGameEntry.fee_payer`, new); a card covers a shortfall (PayShortfall in
  GamePay.js);
- you pay what the game cost when you joined; a full paid game refuses (GAME_FULL) rather than
  charge for a waiting place; checkout picks never charge (join_picks skips paid games, the
  step says so);
- back, once: leaving before the start (or before a start is set), the organiser taking the
  game off, the ticket refunded or voided (which covers a cancelled event). Leaving after the
  start keeps it.

Server: migration 0060_game_fees; vent_event/game_fees.py (join_paid, refund, refund_all);
ledger.quote_game / record_game_fee / reverse_game_fee (and `_line`, `_reverse_lines` carry
game_entry); game_entries.leave refunds, drop_ticket always; views_games: fee_vc on edit,
fee_total_vc in the payload, the paid join (GAME_JOINED_PAID, GAME_FEE_SIGN_IN, GAME_FULL,
INSUFFICIENT_BALANCE with needed_vc), leave answers refunded_vc (GAME_LEFT_REFUNDED), take-off
refunds, fee_paid_vc in the players list and CSV; statement codes gameFee / refundGameFee;
coin source game_fees.refund; card path game_fees.py -> GamePay.js. Also views_push "(s)".
Tests: tests_party_games 51 (GameFeeTests 10), tests_statement_lines; vent_event + vent_auth + vent_tournament 4108 OK.

Frontend: GamePay.js (the sheet), EventGames (tag, "Jouer pour N VC" inside NeedsAccount,
refund-aware leave labels and notices), EventGamesPanel (fee field, hint, paid amounts, the
take-off notice), CheckoutGames (free games only, a line for paid ones), useGameNames +
statementLine's third argument on the five history screens; 29 keys x3 by hand.

Catchers: scripts/backend-dir.mjs + scripts/check-backend-dir.mjs (blocking, self-test 7);
check-undefined now runs react/jsx-no-undef (self-test 8). Seven scripts moved to backend-dir.

Walk: V-ENT/tasks/audit/party-games-g3-walk-2026-10-13.md (headless Chrome for the presses
because the owner had the desktop; emulator for the phone). Local fixture: Cosplay trivia at
50 VC, Chaises musicales back to free.
