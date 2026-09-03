/* The V-ENT overlay runtime.
 *
 * Injected ahead of an uploaded overlay's own scripts by `serve_overlay`. It
 * fetches the tournament, fills anything marked as data, and keeps doing so
 * while the browser source is open.
 *
 * The question this answers: can ANY html file uploaded here be driven by live
 * data. Not quite, and the edge is the design. A file containing
 * `<div>ALIEN X</div>` cannot be driven, because nothing can know whether that
 * is a team name or a word in the artwork, and guessing rewrites the wrong text
 * on air. So the file says which bits are data, with one attribute:
 *
 *     <div data-vent="team.name"></div>
 *     <img data-vent-src="team.logo">
 *     <span data-vent="team.won">0</span>
 *     <tbody data-vent-repeat="standings">
 *       <tr>
 *         <td data-vent="place"></td>
 *         <td data-vent="name"></td>
 *         <td data-vent="won"></td>
 *       </tr>
 *     </tbody>
 *     <div data-vent-show="team.won">Only drawn when they have won something</div>
 *
 * A file that instead defines `window.build()` and reads `window.VENT` gets
 * that object and has `build()` called for it, which is how a scripted overlay
 * like the KON10DR pack is driven with a fifteen-line adapter.
 *
 * Which team the overlay is about comes from its own `?t=TAG`, untouched, so a
 * file written for another system keeps working the way its author expects.
 */
(function () {
  'use strict';

  var tag = document.getElementById('vent-overlay-runtime');
  var FEED = tag && tag.getAttribute('data-feed');
  var EVERY = Number((tag && tag.getAttribute('data-every')) || 4000);
  if (!FEED) return;

  var lastVersion = null;

  /* Which team this overlay is about. The overlay's own convention wins: `?t=`
     is what every pack I have seen uses, and rewriting it would break files
     that already work. */
  function wantedTag() {
    var q = new URLSearchParams(location.search);
    return (q.get('t') || q.get('team') || '').toUpperCase();
  }

  /* `team.name` out of the data, with the row's own fields addressable bare so
     a repeat can say `name` rather than `team.name`. */
  function read(path, scope, data) {
    var parts = String(path).split('|')[0].trim().split('.');
    var root;
    if (parts.length === 1) {
      /* Inside a repeat, `scope` is the row and a bare field means that row's
         own field, which is the documented rule. At the top level `scope` is
         null, and this used to fall back to an empty object, so a bare name
         anywhere outside a repeat resolved to '' silently for ever. Falling
         back to the feed root instead means a bare name means what a reader
         would expect it to mean in both places. */
      root = scope || data;
    } else if (parts[0] === 'tournament') {
      root = data.tournament; parts.shift();
    } else if (parts[0] === 'team') {
      root = data.__team || {}; parts.shift();
    } else if (parts[0] === 'player') {
      root = (data.__team && data.__team.players && data.__team.players[0]) || {};
      parts.shift();
    } else if (parts[0] === 'asset') {
      /* Whatever the organiser assigned that name to in the studio's media
         library. Always read from the feed and never from the row, so
         `asset.hero` means the same thing inside a repeat as outside one.
         Without this branch it fell to the `scope || data` case below and
         resolved to '' inside every repeat, which is the silent kind of wrong
         that is discovered on air. */
      root = data.asset || {}; parts.shift();
    } else {
      root = scope || data;
    }
    var value = root;
    for (var i = 0; i < parts.length; i += 1) {
      if (value === null || value === undefined) return '';
      value = value[parts[i]];
    }
    return value === null || value === undefined ? '' : value;
  }

  /* Everything under `root` carrying `selector`, and `root` itself when it
     carries it too.

     `querySelectorAll` only ever descends, so a repeat whose template IS the
     marked element - `<div data-vent-repeat="pictures"><img data-vent-src="url">`
     - had its rows cloned and then never filled. Nothing errored: the overlay
     drew the right number of empty images. */
  function within(root, selector) {
    var out = [];
    if (typeof root.matches === 'function' && root.matches(selector)) out.push(root);
    root.querySelectorAll(selector).forEach(function (el) { out.push(el); });
    return out;
  }

  function fill(root, scope, data) {
    /* Text. Only written when it differs, so an overlay mid-animation is not
       restarted four times a minute by a value that did not move. */
    within(root, '[data-vent]').forEach(function (el) {
      if (el.closest('[data-vent-repeat]') && el.closest('[data-vent-repeat]') !== root) return;
      var next = String(read(el.getAttribute('data-vent'), scope, data));
      if (el.textContent !== next) el.textContent = next;
    });

    within(root, '[data-vent-src]').forEach(function (el) {
      if (el.closest('[data-vent-repeat]') && el.closest('[data-vent-repeat]') !== root) return;
      var next = String(read(el.getAttribute('data-vent-src'), scope, data));
      if (next && el.getAttribute('src') !== next) el.setAttribute('src', next);
    });

    /* Drawn only when there is something to draw. A zero, an empty string and a
       missing value are all "nothing", because on a stream an empty box is
       worse than an absent one. */
    within(root, '[data-vent-show]').forEach(function (el) {
      var value = read(el.getAttribute('data-vent-show'), scope, data);
      var on = !(value === '' || value === 0 || value === false);
      el.style.display = on ? '' : 'none';
    });
  }

  /* A row of a repeat is usually an object. `pictures` is a list of URLs, and
     a bare string has no fields to address, so it is given the two names a
     designer would reach for. */
  function row_of(row) {
    if (typeof row !== 'string') return row;
    return { url: row, img: row, name: row };
  }

  function repeat(root, scope, data) {
    root.querySelectorAll('[data-vent-repeat]').forEach(function (host) {
      /* A repeat inside another repeat belongs to its row, and is filled when
         that row is built. Filling it here as well would read it from the
         wrong place, and would corrupt the template the outer repeat caches
         from its first child. */
      if (host.parentElement && host.parentElement.closest('[data-vent-repeat]')) return;

      var what = host.getAttribute('data-vent-repeat');
      /* Inside a row, a repeat over one of that row's own lists: a player's
         `pictures`, say. The row wins, because a name that exists on the row
         cannot have meant the one at the top of the feed. */
      var rows = (scope && Array.isArray(scope[what])) ? scope[what]
        : (what === 'players'
          ? ((data.__team && data.__team.players) || [])
          : (data[what === 'standings' ? 'teams' : what] || []));

      /* The first child is the template, kept off-screen rather than removed,
         so the file remains something a designer can open and edit. */
      if (!host.__ventTemplate) {
        var first = host.firstElementChild;
        if (!first) return;
        host.__ventTemplate = first.cloneNode(true);
      }
      host.innerHTML = '';
      rows.forEach(function (raw) {
        var row = row_of(raw);
        var el = host.__ventTemplate.cloneNode(true);
        fill(el, row, data);
        /* Still detached, so a repeat inside it has no outer host above it and
           is built here with this row as its scope. */
        repeat(el, row, data);
        host.appendChild(el);
      });
    });
  }

  function apply(data) {
    var want = wantedTag();
    var teams = data.teams || [];
    data.__team = teams.filter(function (t) {
      return String(t.tag).toUpperCase() === want;
    })[0] || teams[0] || {};

    /* What a scripted overlay reads. Published before `build()` is called,
       because a file that reads it at the top of its own script runs first. */
    window.VENT = data;

    fill(document, null, data);
    repeat(document, null, data);

    if (typeof window.build === 'function') {
      try {
        window.build(data);
      } catch (err) {
        /* An overlay that throws must not take the page down: the rest of it is
           still on screen in front of an audience. */
        if (window.console) console.warn('V-ENT overlay: build() threw', err);
      }
    }

    document.documentElement.setAttribute('data-vent-ready', '1');
  }

  /* One request in flight at a time, and a pause after a refusal.

     On 3 September 2026 an overlay open in OBS asked for the feed about
     twenty-five times a second for six seconds, three times in an hour. A
     four-second timer cannot do that on its own; whatever OBS's browser did
     (a burst of queued timer callbacks on becoming visible is the likeliest),
     the page must not let it through. Every one of those requests counted
     against the organiser's own address at the API, so for a minute
     afterwards their console read "Could not reach the server". */
  var inFlight = false;
  var pausedUntil = 0;
  var failures = 0;

  function poll() {
    if (inFlight) return;
    if (Date.now() < pausedUntil) return;
    inFlight = true;
    fetch(FEED, { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 429 || r.status >= 500) {
          /* Asked to slow down, or the server is struggling: wait longer each
             time, up to a minute, then try again. Nothing on screen changes. */
          failures += 1;
          pausedUntil = Date.now() + Math.min(60000, EVERY * Math.pow(2, failures));
          return null;
        }
        return r.json();
      })
      .then(function (body) {
        if (!body || body.status !== 'success') return;
        failures = 0;
        var data = body.data;
        /* Redraw only when something moved. A browser source runs for six hours
           at a venue, often on a hotspot, and re-rendering an animation every
           four seconds is how an overlay ends up stuttering on air. */
        if (data.version === lastVersion) return;
        lastVersion = data.version;
        apply(data);
      })
      .catch(function () {
        /* Keep whatever is on screen. A scoreboard that freezes at the last
           good numbers is recoverable; one that blanks is not. */
        failures += 1;
        pausedUntil = Date.now() + Math.min(60000, EVERY * Math.pow(2, failures));
      })
      .then(function () { inFlight = false; });
  }

  poll();
  if (EVERY > 0) setInterval(poll, EVERY);

  /* So an organiser can prove the link works before it goes on air. */
  window.VENTOverlay = { refresh: poll, feed: FEED };
})();
