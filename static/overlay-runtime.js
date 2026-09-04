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

  /* Every font the organiser uploaded, declared as a family named by its slot.

     CEO, 3 September 2026, asking whether fonts should be uploaded to the
     studio or carried inside the HTML file: both. This is the studio half. A
     designer writes `font-family: 'hero'` and the organiser decides later what
     hero is, with no URL to paste and nothing to base64.

     The other half needs nothing from here: a font inlined as a data URI is
     part of the file and always works, which is why it stays the safe default.

     Written once and only when the list changes. A stylesheet rebuilt every
     four seconds would make a browser re-evaluate every rule on the page for
     six hours. */
  var fontsWritten = '';

  function writeFonts(fonts) {
    var list = fonts || [];
    var key = list.map(function (f) { return f.slot + ':' + f.url; }).join('|');
    if (key === fontsWritten) return;
    fontsWritten = key;

    var style = document.getElementById('vent-overlay-fonts');
    if (!style) {
      style = document.createElement('style');
      style.id = 'vent-overlay-fonts';
      /* Ahead of the overlay's own styles, so the file can still override
         anything here. It is their design. */
      document.head.insertBefore(style, document.head.firstChild);
    }
    style.textContent = list.map(function (f) {
      /* The slot is already restricted to letters, digits and underscores at
         upload, so it cannot carry a quote out of a font name and into CSS. */
      return "@font-face{font-family:'" + f.slot + "';src:url('" + f.url
        + "') format('" + f.format + "');font-display:swap;}";
    }).join('\n');
  }

  /* -----------------------------------------------------------------------
     Text layers: words an operator put on top of somebody else's design.

     CEO, 4 September 2026, inbox row 52: "also should be able to add text,
     change the font size, color, position, animation of that text also on any
     overlay".

     Handed over in `data-layers` on this script's own tag, which the server
     writes ONLY when the overlay has at least one. **A file with no layers is
     served and drawn exactly as it was**: no attribute, so `readLayers` gives
     an empty list, so nothing below ever runs. No container, no stylesheet, no
     class, nothing appended. That is not a nicety. This is a designer's file,
     opened once at a venue and left running for six hours, and the only safe
     thing to add when there is nothing to add is nothing.

     Everything is measured in pixels at 1920x1080, the raster the rest of the
     studio uses, against a unit that scales with the source: a browser source
     opened 1280 wide draws a 64px caption at 42px, which is what somebody who
     typed 64 meant.

     The entry and exit animations are one shot. Nothing here loops, breathes
     or glows: a graphic that moves on its own is a graphic the audience looks
     at instead of the match. `prefers-reduced-motion` is deliberately NOT
     consulted, and that is the one place this differs from the rest of the
     platform. The preference belongs to whoever is at the machine RENDERING
     the stream, and honouring it would silently stop every caption animating
     for an operator who set it for their own desktop, in front of an audience
     who never expressed it. ----------------------------------------------- */

  var ANCHOR = {
    top_left: 'left:0;top:0;',
    top_centre: 'left:50%;top:0;',
    top_right: 'right:0;top:0;',
    middle_left: 'left:0;top:50%;',
    centre: 'left:50%;top:50%;',
    middle_right: 'right:0;top:50%;',
    bottom_left: 'left:0;bottom:0;',
    bottom_centre: 'left:50%;bottom:0;',
    bottom_right: 'right:0;bottom:0;'
  };

  /* Pulling the box back onto its own anchor. Kept apart from the operator's
     nudge, which is a second translate, so one cannot eat the other. */
  var CENTRING = {
    top_centre: '-50%, 0',
    centre: '-50%, -50%',
    bottom_centre: '-50%, 0',
    middle_left: '0, -50%',
    middle_right: '0, -50%'
  };

  var FAMILY = {
    house: "'ClashGrotesk','Clash Grotesk',system-ui,sans-serif",
    condensed: "'Barlow Condensed',system-ui,sans-serif",
    display: "'Monument Extended',system-ui,sans-serif",
    accent: "'Astronum',system-ui,sans-serif"
  };

  var ENTRY = {
    rise: 'ventLayerRise', fade: 'ventLayerFade',
    slide_left: 'ventLayerInLeft', slide_right: 'ventLayerInRight', none: null
  };
  var EXIT = {
    fade: 'ventLayerOutFade', drop: 'ventLayerOutDrop',
    slide_left: 'ventLayerOutLeft', slide_right: 'ventLayerOutRight', none: null
  };

  var ENTRY_MS = 420;
  var EXIT_MS = 320;

  var LAYER_CSS = [
    /* Inset rather than padded. An absolutely positioned child anchors to its
       containing block's PADDING box, so padding here moved nothing at all and
       a caption at top_left sat in the very corner of the frame. Found by
       measuring the drawn box in a real browser, which is the only place it is
       visible: the markup and the stylesheet both look right. */
    '#vent-layers{--vu:calc(100vw / 1920);position:fixed;',
    'left:calc(var(--vu) * 48);top:calc(var(--vu) * 48);',
    'right:calc(var(--vu) * 48);bottom:calc(var(--vu) * 48);',
    'pointer-events:none;z-index:2147483000;}',
    '#vent-layers .vl{position:absolute;}',
    '#vent-layers .vl-in{display:block;white-space:pre-wrap;line-height:1.12;',
    'margin:0;visibility:hidden;}',
    '#vent-layers .vl-on{visibility:visible;}',
    '@keyframes ventLayerRise{from{opacity:0;',
    'transform:translateY(calc(var(--vu) * 28))}to{opacity:1;transform:none}}',
    '@keyframes ventLayerFade{from{opacity:0}to{opacity:1}}',
    '@keyframes ventLayerInLeft{from{opacity:0;',
    'transform:translateX(calc(var(--vu) * -60))}to{opacity:1;transform:none}}',
    '@keyframes ventLayerInRight{from{opacity:0;',
    'transform:translateX(calc(var(--vu) * 60))}to{opacity:1;transform:none}}',
    '@keyframes ventLayerOutFade{from{opacity:1}to{opacity:0}}',
    '@keyframes ventLayerOutDrop{from{opacity:1;transform:none}',
    'to{opacity:0;transform:translateY(calc(var(--vu) * 28))}}',
    '@keyframes ventLayerOutLeft{from{opacity:1;transform:none}',
    'to{opacity:0;transform:translateX(calc(var(--vu) * -60))}}',
    '@keyframes ventLayerOutRight{from{opacity:1;transform:none}',
    'to{opacity:0;transform:translateX(calc(var(--vu) * 60))}}'
  ].join('');

  function readLayers() {
    var raw = tag && tag.getAttribute('data-layers');
    if (!raw) return [];
    try {
      var list = JSON.parse(raw);
      return Array.isArray(list) ? list : [];
    } catch (err) {
      /* A caption must never take an on-air page down. */
      if (window.console) console.warn('V-ENT overlay: layers unreadable', err);
      return [];
    }
  }

  /* Every value is checked again here even though the API already refused the
     bad ones. This string goes straight into `style.cssText`, and one value
     that is not what it claims to be does not break that caption, it breaks
     the whole declaration block. */
  function whole(value, low, high, fallback) {
    var number = Number(value);
    if (!isFinite(number)) return fallback;
    number = Math.round(number);
    return Math.min(high, Math.max(low, number));
  }

  function colourOf(value) {
    return /^#([0-9a-f]{6}|[0-9a-f]{8})$/i.test(String(value || ''))
      ? String(value) : '#FFFFFF';
  }

  function familyOf(layer) {
    var slot = String(layer.font_slot || '');
    /* A font the organiser uploaded, declared by `writeFonts` above under this
       same name. It wins, because they chose it for this broadcast. */
    if (/^[a-z0-9_]{1,40}$/.test(slot)) return "'" + slot + "',sans-serif";
    return FAMILY[layer.family] || FAMILY.house;
  }

  function alignOf(value) {
    if (value === 'left' || value === 'right') return value;
    return 'center';
  }

  function placeOf(value) {
    /* `as_designed` is in the shared list because a GRAPHIC can be left where
       its own design put it. Words have no design of their own, so it means
       what a caption means with nothing set: the bottom centre. */
    return ANCHOR[value] ? value : 'bottom_centre';
  }

  var LAYERS = readLayers();
  var layerNodes = null;
  var lastData = null;

  function schedule(node, layer) {
    var delay = whole(layer.delay_ms, 0, 60000, 0);
    var live = whole(layer.duration_ms, 0, 600000, 0);
    var entry = ENTRY[layer.entry];
    var leave = EXIT[layer.exit];

    setTimeout(function () {
      node.className = 'vl-in vl-on';
      if (entry) node.style.animation = entry + ' ' + ENTRY_MS + 'ms ease-out both';
      /* 0 means it stays until the overlay goes, the same word the rest of the
         studio uses for a duration. */
      if (live <= 0) return;
      setTimeout(function () {
        if (!leave) { node.className = 'vl-in'; return; }
        node.style.animation = leave + ' ' + EXIT_MS + 'ms ease-in both';
        setTimeout(function () { node.className = 'vl-in'; }, EXIT_MS);
      }, live);
    }, delay);
  }


  /* ------------------------------------------------- what a layer paints

     CEO, 4 September 2026, inbox row 51: "there should be elements you can add
     or ways to add certan uploaded things like images, sponsor logos, player
     images or videos as like elements that will then be movable inside an
     element once they are loaded".

     A caption and a sponsor logo differ in what is drawn and in nothing else,
     so everything around this, the anchor, the nudge, the order, the entrance,
     the delay, is the same code for both. Only these two functions differ. */

  function wordsOf(layer) {
    var inner = document.createElement('div');
    inner.className = 'vl-in';
    inner.style.cssText = 'font-family:' + familyOf(layer) + ';'
      + 'font-size:calc(var(--vu) * ' + whole(layer.font_size, 8, 400, 64) + ');'
      + 'font-weight:' + whole(layer.weight, 100, 900, 600) + ';'
      + 'color:' + colourOf(layer.colour) + ';';
    /* textContent, never innerHTML. An operator types a caption, and a caption
       is words. */
    inner.textContent = String(layer.text || '');
    return inner;
  }

  function mediaOf(layer) {
    if (layer.kind !== 'asset') return null;
    var src = String(layer.asset_url || '');
    /* A layer pointing at media that has been deleted draws nothing at all.
       The alternative is the browser's broken-image glyph, on air. */
    if (!src) return null;

    var video = layer.asset_kind === 'video';
    var node = document.createElement(video ? 'video' : 'img');
    node.className = 'vl-in';
    node.src = src;
    if (video) {
      /* A clip in a layer is decoration, not the programme: it plays itself,
         says nothing, and loops rather than freezing on a last frame. */
      node.muted = true;
      node.autoplay = true;
      node.loop = true;
      node.playsInline = true;
    } else {
      node.alt = '';
    }

    /* Width in pixels at 1920x1080, height from the media's own proportions:
       an operator stretching a sponsor's logo is a conversation nobody wants
       to have. Zero means whatever size the file is.

       And it is never drawn above its natural size. That is the rule the whole
       platform holds to, because no filter adds detail a file never had, and a
       logo blown up four times on a broadcast is the most visible way to break
       it. The cap is applied once the browser knows the natural width. */
    var asked = whole(layer.width_px, 0, 1920, 0);
    if (asked > 0) {
      node.style.width = 'calc(var(--vu) * ' + asked + ')';
    }
    node.style.height = 'auto';
    node.style.display = 'block';

    var capNatural = function () {
      var natural = video ? node.videoWidth : node.naturalWidth;
      if (!natural || !asked) return;
      if (asked > natural) node.style.width = 'calc(var(--vu) * ' + natural + ')';
    };
    node.addEventListener(video ? 'loadedmetadata' : 'load', capNatural);

    /* A file that will not load takes its own layer away rather than leaving a
       gap where something was cued. */
    node.addEventListener('error', function () {
      if (node.parentNode) node.parentNode.style.display = 'none';
    });
    return node;
  }

  function buildLayers() {
    if (!LAYERS.length || layerNodes) return;

    var style = document.createElement('style');
    style.id = 'vent-layers-css';
    style.textContent = LAYER_CSS;
    (document.head || document.documentElement).appendChild(style);

    var host = document.createElement('div');
    host.id = 'vent-layers';
    (document.body || document.documentElement).appendChild(host);

    layerNodes = [];
    LAYERS.forEach(function (layer) {
      var place = placeOf(layer.position);
      var outer = document.createElement('div');
      outer.className = 'vl';
      outer.style.cssText = ANCHOR[place]
        + 'z-index:' + whole(layer.order, 0, 999, 0) + ';'
        + 'text-align:' + alignOf(layer.align) + ';'
        + 'transform:translate(' + (CENTRING[place] || '0, 0') + ')'
        + ' translate(calc(var(--vu) * ' + whole(layer.offset_x, -800, 800, 0)
        + '), calc(var(--vu) * ' + whole(layer.offset_y, -800, 800, 0) + '));';

      var inner = mediaOf(layer) || wordsOf(layer);

      outer.appendChild(inner);
      host.appendChild(outer);
      layerNodes.push({ layer: layer, node: inner });
      schedule(inner, layer);
    });

    /* The feed may well have answered before the document was ready, in which
       case a layer bound to a feed path would have shown its fallback until
       something else changed. */
    if (lastData) fillLayers(lastData);
  }

  /* A layer bound to a feed path, kept up to date. `text` is what is drawn
     when the path resolves to nothing, which is the whole reason both exist:
     a caption on a fixture nobody has picked yet must say something. */
  function fillLayers(data) {
    if (!layerNodes) return;
    layerNodes.forEach(function (entry) {
      /* Only words are filled from the feed. Writing textContent onto an image
         would empty the element it is drawn by. */
      if (entry.layer.kind === 'asset') return;
      var path = entry.layer.field;
      if (!path) return;
      var value = read(path, null, data);
      var next = (value === '' || value === null || value === undefined)
        ? String(entry.layer.text || '') : String(value);
      if (entry.node.textContent !== next) entry.node.textContent = next;
    });
  }

  function apply(data) {
    var want = wantedTag();
    writeFonts(data.fonts);
    var teams = data.teams || [];
    data.__team = teams.filter(function (t) {
      return String(t.tag).toUpperCase() === want;
    })[0] || teams[0] || {};

    /* What a scripted overlay reads. Published before `build()` is called,
       because a file that reads it at the top of its own script runs first. */
    window.VENT = data;

    fill(document, null, data);
    repeat(document, null, data);

    /* Kept, so a layer built after the first feed arrived still gets its text.
       Filled before `build()` is called, because an overlay that throws must
       not be the reason a caption is left saying the wrong thing. */
    lastData = data;
    fillLayers(data);

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


  /* -------------------------------------------------- where the file sits

     CEO, 4 September 2026: "should be able to change position even for the
     overlays you upload". The Sits control reached V-ENT's own graphics only,
     and what I had told them, that an uploaded file is moved by editing its
     own CSS, is true and is not an answer: the person holding the file at a
     venue is an operator, not its designer.

     An uploaded file positions itself however its designer chose, so moving it
     means measuring what it actually drew and translating that. The drawn box
     is the union of every visible element's rectangle, not the body's own,
     because a body that fills the frame while its content sits bottom left is
     the normal case and its rectangle says nothing.

     Then the box is moved so it sits at the requested anchor inside a 5%
     safe area, which is 96px across and 54px down at 1920x1080, the same
     margin the studio's own graphics use.

     Three things this deliberately does NOT do:

     - it never runs when nobody has moved the overlay. No attribute, no
       measurement, no transform, and the file is byte for byte what its
       designer wrote
     - it does not scale. Only translation, so nothing is ever drawn above its
       own resolution by a control meant to nudge it
     - it does not fight the file. A design that animates its own position
       keeps animating: the transform is on a wrapper the runtime owns, and the
       file's own transforms are untouched underneath. */

  function readSits() {
    var raw = tag && tag.getAttribute('data-sits');
    if (!raw) return null;
    try {
      var value = JSON.parse(raw);
      return value && value.position ? value : null;
    } catch (err) {
      if (window.console) console.warn('V-ENT overlay: position unreadable', err);
      return null;
    }
  }

  var SITS = readSits();

  /* The union of what the document actually painted. Elements with no size,
     and the wrapper itself, are skipped: an empty container that stretches to
     the frame would otherwise make the box the whole frame and the move a
     no-op, which reads as the control being broken. */
  function drawnBox() {
    var nodes = document.body ? document.body.querySelectorAll('*') : [];
    var left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (var i = 0; i < nodes.length; i += 1) {
      var el = nodes[i];
      if (el.id === 'vent-sits' || el.id === 'vent-layers') continue;
      var style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden'
          || Number(style.opacity) === 0) continue;
      var box = el.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) continue;
      if (box.width >= window.innerWidth && box.height >= window.innerHeight) continue;
      left = Math.min(left, box.left);
      top = Math.min(top, box.top);
      right = Math.max(right, box.right);
      bottom = Math.max(bottom, box.bottom);
    }
    if (!isFinite(left) || right <= left) return null;
    return { left: left, top: top, width: right - left, height: bottom - top };
  }

  function placeFile() {
    if (!SITS || !document.body) return;

    var wrap = document.getElementById('vent-sits');
    if (!wrap) {
      /* A wrapper the runtime owns, so the file's own transforms are left
         alone. Everything the document drew moves into it once. */
      wrap = document.createElement('div');
      wrap.id = 'vent-sits';
      wrap.style.cssText = 'position:absolute;left:0;top:0;width:100%;'
        + 'height:100%;transform-origin:top left';
      while (document.body.firstChild) {
        wrap.appendChild(document.body.firstChild);
      }
      document.body.appendChild(wrap);
    }

    /* Measured with the wrapper back at zero, or the second call would move it
       again by the amount of the first. */
    wrap.style.transform = 'none';
    var box = drawnBox();
    if (!box) return;

    var frameW = window.innerWidth;
    var frameH = window.innerHeight;
    var safeX = frameW * 0.05;
    var safeY = frameH * 0.05;

    var wantLeft = box.left;
    var wantTop = box.top;
    var where = String(SITS.position || '');

    if (where.indexOf('left') !== -1) wantLeft = safeX;
    else if (where.indexOf('right') !== -1) wantLeft = frameW - safeX - box.width;
    else if (where.indexOf('centre') !== -1 || where === 'centre') {
      wantLeft = (frameW - box.width) / 2;
    }

    if (where.indexOf('top') === 0) wantTop = safeY;
    else if (where.indexOf('bottom') === 0) wantTop = frameH - safeY - box.height;
    else if (where.indexOf('middle') === 0 || where === 'centre') {
      wantTop = (frameH - box.height) / 2;
    }

    /* The nudge is in pixels at 1920x1080, so it scales with the frame the
       same way every other measurement on a broadcast graphic does. */
    var unit = frameW / 1920;
    var dx = wantLeft - box.left + (Number(SITS.offset_x) || 0) * unit;
    var dy = wantTop - box.top + (Number(SITS.offset_y) || 0) * unit;

    wrap.style.transform = 'translate(' + Math.round(dx) + 'px,'
      + Math.round(dy) + 'px)';
  }

  /* Re-measured after the fonts land, because a headline in the fallback face
     is a different width and the box would be wrong by exactly that. */
  function watchPlacement() {
    if (!SITS) return;
    placeFile();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(placeFile).catch(function () {});
    }
    /* Once more after the first paint settles, and on resize, which is what a
       browser source does when somebody changes its size in OBS. */
    setTimeout(placeFile, 400);
    window.addEventListener('resize', placeFile);
  }

  /* The runtime is injected into the head, ahead of the document it decorates,
     so there is usually no body to append a layer to yet. */
  function whenReady(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  whenReady(buildLayers);
  whenReady(watchPlacement);

  poll();
  if (EVERY > 0) setInterval(poll, EVERY);

  /* So an organiser can prove the link works before it goes on air. */
  window.VENTOverlay = { refresh: poll, feed: FEED };
})();
