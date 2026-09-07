# Handover, 4 September 2026, fourth session

"CONTINUE THE REST OF THE UNFINISHED". Five open asks closed, on top of the
morning's deploy. Everything below is pushed to `feature/run-of-show` and is
**NOT deployed**: main was last deployed at about 10:15 UTC and carries the run
of show, the door fixes, the Rivalry graphics, text layers and the event
organisation path.

---

## What closed

| Row | Ask | Where |
|---|---|---|
| 63 | "how to add events or tournaments to an organization? i dont see that path" | BE `348b8a43`, FE `91ef3c0` |
| 66 | "what does it also mean by organizer has not pinned?" | FE `91ef3c0` |
| 53 | "the ui of the production ... no sub categories" | FE `86f2304` |
| 50 | "change position even for the overlays you upload" | BE `5359078e`, FE `6f3b7a4` |
| 51 | "elements you can add ... images, sponsor logos, player images or videos" | BE `81d4de97`, FE `3906130` |

## 63, and the shape it had

The event half shipped in the morning. The tournament half was the same fault
one level along: `tournament_organization` has been on the model since
organisations were built and both endpoints have accepted it the whole time,
and **no payload ever said what it was set to**, so no screen could show it and
the edit screen had nothing to fill a picker from. A column nothing reads and
nothing writes is a column that does not exist.

The detail payload carries `organization` now, as null rather than an absent
key on a personal tournament: a key that is sometimes missing is a key every
screen has to guess at. Two rows joined `check-parity.py` for this pair, one
for the payload and one for the screen.

## 66, which was two problems wearing one sentence

"The organiser has not pinned this venue on a map yet" named a column, and
nobody could act on it either way, because `map_link` has been accepted by the
edit endpoint all along and **no screen ever sent it**.

The public sentence now says what a reader wants: there is no map, and the
address below is what the organiser gave. The console gained "Where it is",
which asks for the venue name, the Google Maps link and how to find the place.
It asks for the LINK because `Event.save()` reads the coordinate out of it, and
typing two numbers by hand is the step where a venue ends up in the Gulf of
Guinea.

## 53, and what the scroll actually was

Sections, and one graphic open at a time. The sections alone would not have
fixed it: the length came from twenty three cards each carrying a URL, a live
preview, its payload fields, four presentation controls and its text layers.
A card is a row until it is opened now, and a card with unsaved typing stays
open, or pressing another would look like it threw the typing away.

## 50, and how an uploaded file gets moved

An uploaded overlay positions itself however its designer chose, so the runtime
measures **what the document actually painted**: the union of every visible
element's rectangle, skipping anything that fills the frame, because a body
covering 1920x1080 while its content sits bottom left is the normal case and
its own rectangle says nothing. That box is translated to the requested anchor
inside the same 5 per cent safe area the studio graphics use.

Translation only, never scale. The transform sits on a wrapper the runtime
owns, so a design that animates its own position keeps animating underneath.
Re-measured after `document.fonts.ready`, because a headline in the fallback
face is a different width.

**`as_designed` means nothing is injected at all**: no attribute on the script
tag, no wrapper, no measurement. The test asserts that on the SERVED HTML
rather than on the model, because an empty column proves nothing about what the
browser receives. An overlay already pasted into a machine at a venue must not
move because this shipped.

## 51, and why it is one model

`OverlayTextLayer` became `OverlayLayer` with a `kind`. A caption and a
sponsor's logo differ in what is painted and in nothing else, so the anchor,
the nudge, the order, the entrance, the delay and the on/off are one
implementation. A second table would have been the same feature built twice.

Three rules it carries: media comes from the studio's own library and nowhere
else, so an overlay never depends on a host somewhere else staying up; width
only, capped at the media's natural size; and media that has been deleted nulls
the layer's foreign key so the page draws nothing rather than a broken-image
glyph on air.

**The migration is written by hand.** `makemigrations` proposed CreateModel plus
DeleteModel, which drops the table and every row in it.

---

## Verified

- `manage.py test vent_tournament vent_event`: **1748 tests, OK**
- `tools/check-parity.py`: 16 pairs, none built on one side only
- frontend: check-keys 0 missing, dict-parity en=fr=pt (44 new strings written
  by hand in all three today), check-design 0 new, check-css-classes 0 severe,
  check-control-bytes 0
- every changed screen compiles and serves

## NOT verified, and not claimed

- **No browser walk of any of it.** The connected Chrome is a Remote Control
  device and cannot reach this machine's localhost: every navigation to
  127.0.0.1 answers ERR_CONNECTION_REFUSED while curl on the same address
  answers 200. Compilation and the checkers are what stands behind this work.
- **The runtime's placement is unproven in a browser.** `drawnBox()` measures a
  real document and no test exercises it against one. It is the piece I would
  look at first.
- **The console's new controls have not been pressed**: the sections, the
  organisation pickers, the venue panel, the Sits control on an uploaded
  overlay, the media chooser on a layer.

## Still open

- **47**: 26 endpoints with no screen able to reach them, baselined in
  `tools/endpoint-callers-baseline.json` and listed in `tasks/inbox.md`
- **69**: the ten Rivalry Series player names, waiting on the CEO
- **55, 56**: the overlay files were never sent
- A V-ENT house drawing for the four new broadcast kinds, which exist in the
  Rivalry look only
