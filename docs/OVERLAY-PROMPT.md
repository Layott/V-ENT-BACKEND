# Turning any design into a V-ENT stream overlay

CEO, 29 August 2026: "maybe there could be a way for us to give users prompts
that they use to generate or convert whatever stream element they want into an
editable html file usable on our website."

This is that prompt, and the contract behind it.

---

## The short version of what is and is not possible

**Any HTML file can be uploaded and will render.** It gets a URL, and the URL
goes into an OBS or vMix browser source.

**An unmarked file cannot be driven, and nothing can fix that.** Given
`<div>ALIEN X</div>`, nothing can know whether that is a team name, a sponsor,
or a word in the artwork. Guessing rewrites the wrong text on air, which is
worse than an overlay that does nothing. V-ENT tells you at upload when a file
is in this state rather than letting you find out during a final.

**Marking it takes one attribute per element.** That is the whole contract, it
survives being opened in any editor, and it is exactly the kind of edit an
assistant can make from the prompt below.

---

## The contract

```html
<!-- one value -->
<h1 data-vent="tournament.title"></h1>
<div data-vent="team.name"></div>
<span data-vent="team.won">0</span>

<!-- an image -->
<img data-vent-src="team.logo" alt="">
<img data-vent-src="player.img" alt="">

<!-- a list: the first child is the template, repeated per row -->
<tbody data-vent-repeat="standings">
  <tr>
    <td data-vent="place"></td>
    <td data-vent="name"></td>
    <td data-vent="won"></td>
  </tr>
</tbody>

<!-- drawn only when there is something to draw -->
<div data-vent-show="team.won">Winner of {{n}} matches</div>
```

Inside a `data-vent-repeat`, a row's own fields are addressed without a prefix:
`name`, not `team.name`.

### Every name that can be filled

| Where | Names |
|---|---|
| `tournament.` | `title`, `game`, `logo` |
| `team.` | `tag`, `name`, `logo`, `place`, `played`, `won`, `lost`, `points_for`, `points_against` |
| `player.` | `ign`, `id`, `img` |
| `data-vent-repeat` | `standings`, `teams`, `players`, `live` |

Anything else is reported at upload as a name that will stay empty, so a typo
is caught before the overlay is on a screen in a hall.

### Which team the overlay is about

`?t=TAG` on the URL, which is the convention every overlay pack already uses.
One uploaded file therefore serves every team: point one browser source at
`?t=AX` and another at `?t=SKG`.

### If you would rather write JavaScript

Define `window.build()`. V-ENT publishes `window.VENT` with the whole feed
before your scripts run, and calls `build()` again whenever the data changes.
This is how a pack that already renders itself from a data object is adapted:
point its lookup at `window.VENT.teams` and it is done.

---

## The prompt

Paste this, with your design file:

> I have an HTML stream overlay. I want to upload it to V-ENT so it fills itself
> from a live tournament and updates while the stream is running.
>
> Please edit my file so the parts that should follow the tournament are marked,
> and change nothing else. Keep every style, animation, font and image exactly
> as it is. The markup is otherwise mine and I need to be able to open it and
> recognise it.
>
> The rules:
>
> - a single value gets `data-vent="..."`, and its existing text stays as the
>   placeholder so the file still looks right when I open it on its own;
> - an image gets `data-vent-src="..."` instead of having its `src` replaced;
> - a repeating list (a standings table, a roster) gets `data-vent-repeat="..."`
>   on the container, and I keep exactly one child inside it as the template;
>   inside it, address the row's own fields with no prefix;
> - something that should disappear when there is no value gets
>   `data-vent-show="..."`.
>
> The names available are:
> `tournament.title`, `tournament.game`, `tournament.logo`,
> `team.tag`, `team.name`, `team.logo`, `team.place`, `team.played`,
> `team.won`, `team.lost`, `team.points_for`, `team.points_against`,
> `player.ign`, `player.id`, `player.img`.
> Repeats: `standings`, `teams`, `players`, `live`.
>
> Use only those names. If something in my design has no matching name, leave it
> exactly as it is and tell me at the end which parts you left alone and why.
>
> Do not add a script, do not fetch anything, and do not add an iframe: V-ENT
> serves the file from its own domain and injects the runtime itself.

---

## Uploading it

Tournament console, Production tab, Overlays. You get back:

- **the URL** to paste into OBS or vMix as a browser source;
- **what was found** in your file, so you can see it read the marks you made;
- **anything it could not drive**, said before you go on air.

Rotate the URL if a machine at a venue has had it for a while. The old one stops
working immediately, which is the point of a token.

## Why the URL is public

A browser source has no session, no cookie and no way to sign in. Whatever
authorises it has to be in the URL. So the token is long and random, the page is
public, and the URL is treated as a secret. The overlay shows the same standings
as the public tournament page, to a camera pointed at a screen.

## Proving it before you go live

```
node V-ENT-FRONTEND/scripts/overlay-probe.mjs "<your overlay URL>" \
  "https://v-ent.co/tournament/<slug>/overlay-feed/"
```

It opens the URL in a real browser at 1920x1080, waits for the overlay to draw,
and prints the feed next to what actually rendered.
