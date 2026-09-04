# The Futbin scraper: what it is, and what it has actually done

> CEO, 4 September 2026: "Are the player cards on the website the actual cards
> from futbin? was any scraping done to get the fully updated images? do you
> have the futbin scraper configured for v-ent?"

Straight answers first.

| Question | Answer as of 4 September 2026 |
|---|---|
| Are the cards on the site real Futbin cards? | **No.** They are 25 rows hand-written in `seed_cards.py --demo`. |
| Has any scraping been done? | **No.** Not once. |
| Is the scraper configured? | **On this machine, yes, as of today.** Playwright and Chromium are installed and the scraper reaches Futbin. It stops at the Cloudflare gate, which a person has to answer once. |

## What the demo data actually is

`python manage.py seed_cards --demo` writes 25 cards with real names, ratings,
clubs, nations and six stats each. They are a fixture so the picker, the
lineup, the rules and the overlay can be built and tested without a scraper.

It carries **no images at all**, deliberately. It used to build a Futbin CDN
address out of an EA player id, and the ids in that table were written by hand.
Rendered side by side on 4 September the famous ones were right and the guessed
ones were not: "Victor Osimhen" showed Phil Foden, "Wilfred Ndidi" and "Samuel
Chukwueze" showed white players, and "Bruno Onyemaechi" answered 404. A card
showing one player's face under another player's name is worse than a card with
no face, so the seed now ships none and `FutCard` draws initials, which is a
designed state.

The frame art was worse: `cdn.futbin.com/design/img/cards/tiny/<type>.png`
answers 404 for every type, so every seeded card fell back to a plain coloured
band. That is what "the cards dont carry the full design" was. `FutCard` now
draws the whole card from the data, so a card is complete with no network at
all, and Futbin's art is a layer on top when it loads.

**Real images come from the scraper**, which reads both addresses off the page
rather than building them from an id.

## Running it

It runs on a desktop, not on the VPS, and the reason is in the file's own
header: Futbin is behind Cloudflare, a plain request answers 403, and so does a
headless browser from a datacentre address. What works is a real Chromium
window from a residential or VPN exit with a profile that keeps the clearance
cookie between runs.

Setup, once, in `V-ENT-BACKEND/`:

```bash
pnpm add -D playwright              # done on this machine, 4 September 2026
pnpm exec playwright install chromium
```

Then set two values, in the environment or in `V-ENT-BACKEND/.env`:

```
V_ENT_API=https://api.v-ent.co
CARDS_INGEST_KEY=<the same value the server has>
```

The server reads `CARDS_INGEST_KEY` from its own environment and answers 503
`INGEST_NOT_CONFIGURED` until it is set, so nothing can be written to the
catalogue by accident.

Running:

```bash
node tools/scrape-futbin.mjs --dry-run --pages 1   read one page, write nothing
node tools/scrape-futbin.mjs --pages 3             the newest three pages
node tools/scrape-futbin.mjs                       delta, stops when caught up
node tools/scrape-futbin.mjs --full --pages 200    a full sweep
node tools/scrape-futbin.mjs --self-test           the parsing, 7 fixtures
```

## The Cloudflare gate, and who answers it

On the first run a Chromium window opens and Cloudflare challenges it. The
scraper prints:

```
Cloudflare is asking. Solve it in the window, then press Enter here.
```

**A person answers that, not an agent.** Solving or working around a bot check
is not something I will do, and it is also not something worth automating: the
profile in `tools/.futbin-profile` keeps the clearance afterwards, so it is one
click and then later runs are unattended until the cookie expires.

That is exactly where the first real run stopped on 4 September 2026, which is
why the catalogue still holds only the demo fixture.

## What a real scrape changes

Once it has run, every card in the picker carries Futbin's own portrait and
card art, the item type and variant are read from the frame file rather than
guessed, and prices arrive. Nothing else has to change: the picker, the rules
engine and the overlay all read `GameCard` and do not care where a row came
from.

A field the scraper cannot read is **left out of the row**, never sent as null.
The server treats an absent field as "leave it alone" and a null as "clear it",
which is the difference between a scrape that failed to read a price and a
scrape that erased one.
