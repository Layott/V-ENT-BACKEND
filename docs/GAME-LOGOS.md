# Game logos: where they may come from

Status: **a decision is needed before any logo ships with the catalogue.** This
document states the gap rather than guessing past it, which is what gate B4 asks
for.

## What is actually on the platform today

17 games in the catalogue, 6 with a logo. Every one of those six was uploaded
through the admin console by somebody at V-ENT: the filenames are the console's
own (`2_logo_uSESlAn.jpg`), not downloads.

**No logo has been sourced or added by an automated pass.** The games research
added the titles and their editions; it deliberately did not add artwork.

## Why this needed stopping

A publisher's logo is a trademark. Garena's Free Fire mark, Krafton's PUBG mark,
EA's FC mark and Riot's Valorant mark are not ours, and they are not made ours by
being small, by being on a dark background, or by being fetched from a page that
served them without a licence.

Two separate risks, and only one of them is about copyright:

1. **Trademark.** Using a mark in a way that suggests the publisher endorses or
   runs the tournament. This is the one that gets a letter.
2. **Provenance.** A logo pulled off a fan wiki is very often the wrong one - an
   old edition, a regional variant, or a fan redraw. The
   art-provenance rule in the workspace CLAUDE.md exists because that has
   already happened once on another project: a logo that matched by name and not
   by shape.

## The three options, and what each costs

| Option | What it means | Cost |
|---|---|---|
| **A. Press kits** | Most publishers run a press or brand page with downloadable marks and written usage terms. Use only those, record the URL and the date for each, and follow the stated rules (usually: do not modify, do not imply endorsement). | A person's afternoon, per batch. Free. Defensible. |
| **B. No publisher art** | The catalogue shows the game's name in the platform's own type on a neutral tile. No mark, no risk, and it looks deliberate rather than unfinished if the tiles are designed rather than left empty. | A design pass. Nothing recurring. |
| **C. Ask each publisher** | Written permission. Only worth it for the handful of titles V-ENT actually runs at scale, and only once there is a reason for them to answer. | Weeks, and most will not reply. |

**A is the ordinary answer**, with B as the fallback for any title whose press
kit does not permit this use. C is worth starting only for the two or three
titles that carry the platform.

## What must be true of any logo that does ship

Per the art-provenance rule, and these are not optional:

- **Its source is recorded** - the exact URL and the date it was taken, on the
  game record itself, not in somebody's memory. "Where did that logo come from"
  is a question that gets asked, and a screenshot of a Google image search is not
  an answer.
- **It is never drawn above its own resolution.** A 128px mark rendered at 420px
  is a 3.3x stretch and no filter adds detail that was never in the file. Get the
  vector, trace it from the raw coverage gradient, or draw it smaller.
- **It is verified to be the same drawing**, not merely the same name, when it
  comes from anywhere other than the publisher. Render both, normalise each to
  its own bounding box, compare by intersection over union.

## What is needed from the CEO

One line: **A, B, or C**. Nothing ships until then, and the catalogue is fully
usable without logos in the meantime - the console already accepts an upload per
game, so a decision can be applied without any further code.
