// Does the overlay runtime actually fill what it says it fills.
//
// The runtime is the one piece of this system with no test, and it is also the
// piece that fails silently: a name it cannot resolve becomes '' and the
// overlay goes on air looking finished with an empty box in it. That fault has
// now happened twice, so per the catcher rule it gets a checker.
//
// There is no DOM library on this machine and the pnpm store here has a history
// of breaking, so this ships its own DOM: only the handful of calls the runtime
// makes, and element trees built in JavaScript rather than parsed from HTML.
// That is enough to exercise read(), fill() and repeat() against real feed
// shapes, which is where the faults are.
//
//   node tools/check-overlay-runtime.mjs
//   node tools/check-overlay-runtime.mjs --self-test
//
// The self-test breaks the runtime on purpose and fails if the checker still
// passes. A checker that cannot fail reports "clean" when it is broken, which
// is how check-signed-out reached 0 three times with the bug still there.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME = path.join(HERE, '..', 'static', 'overlay-runtime.js');

/* ------------------------------------------------------------------ the DOM */

class El {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = String(tag).toUpperCase();
    this.attrs = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.style = {};
    this._text = '';
    children.forEach((c) => this.appendChild(c));
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }

  setAttribute(name, value) { this.attrs[name] = String(value); }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  get firstElementChild() { return this.children[0] || null; }

  set innerHTML(value) {
    if (value !== '') throw new Error('the shim only supports innerHTML = ""');
    this.children.forEach((c) => { c.parentElement = null; });
    this.children = [];
  }

  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((c) => c.textContent).join('');
  }

  set textContent(value) {
    this.children.forEach((c) => { c.parentElement = null; });
    this.children = [];
    this._text = String(value);
  }

  cloneNode() {
    const copy = new El(this.tagName, this.attrs);
    copy._text = this._text;
    copy.style = { ...this.style };
    this.children.forEach((c) => copy.appendChild(c.cloneNode(true)));
    return copy;
  }

  // Only `[attribute]` selectors, which is all the runtime uses.
  matches(selector) {
    const m = /^\[([a-z-]+)\]$/.exec(selector);
    if (!m) throw new Error('the shim only supports [attr] selectors, got ' + selector);
    return Object.prototype.hasOwnProperty.call(this.attrs, m[1]);
  }

  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      node.children.forEach((child) => {
        if (child.matches(selector)) out.push(child);
        walk(child);
      });
    };
    walk(this);
    return out;
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches && node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }

  find(id) {
    if (this.attrs.id === id) return this;
    for (const child of this.children) {
      const hit = child.find(id);
      if (hit) return hit;
    }
    return null;
  }
}

const el = (tag, attrs, children) => new El(tag, attrs, children);

/* -------------------------------------------------------------- the fixture */

// A feed shaped exactly like the one views_overlay_feed publishes.
const FEED = {
  version: 'v1',
  tournament: { title: 'Slot Cup', game: 'EA FC 26', logo: '/media/cup.png' },
  asset: {
    hero: '/media/studio/hero.png',
    bug: '/media/studio/bug.png',
  },
  assets: [
    { name: 'Hero shot', kind: 'image', url: '/media/studio/hero.png', slot: 'hero' },
  ],
  teams: [
    {
      tag: 'ALPHA',
      name: 'Test Alpha',
      logo: '/media/alpha.png',
      place: 1,
      won: 1,
      players: [
        {
          ign: 'zainab',
          img: '/media/studio/zainab-1.png',
          pictures: ['/media/studio/zainab-1.png', '/media/studio/zainab-2.png'],
        },
        { ign: 'musa', img: null, pictures: [] },
      ],
    },
    { tag: 'BRAVO', name: 'Test Bravo', logo: '/media/bravo.png', place: 2, won: 0, players: [] },
  ],
};

function buildPage() {
  const runtimeTag = el('script', { id: 'vent-overlay-runtime', 'data-feed': '/feed/', 'data-every': '0' });

  // A hero picture at the top level, plus one inside a repeat over teams.
  const hero = el('img', { id: 'hero', 'data-vent-src': 'asset.hero' });
  const title = el('h1', { id: 'title', 'data-vent': 'tournament.title' });

  const standings = el('tbody', { id: 'standings', 'data-vent-repeat': 'standings' }, [
    el('tr', {}, [
      el('td', { 'data-vent': 'place' }),
      el('td', { 'data-vent': 'name' }),
      // The regression: a studio picture addressed from inside a repeat.
      el('img', { 'data-vent-src': 'asset.bug' }),
      el('span', { 'data-vent-show': 'won' }, [el('i', {})]),
    ]),
  ]);

  // A repeat inside a repeat: every picture the studio holds of each player.
  const players = el('div', { id: 'players', 'data-vent-repeat': 'players' }, [
    el('div', {}, [
      el('span', { 'data-vent': 'ign' }),
      el('div', { 'data-vent-repeat': 'pictures' }, [
        el('img', { 'data-vent-src': 'url' }),
      ]),
    ]),
  ]);

  const unknown = el('span', { id: 'unknown', 'data-vent': 'nothing.at.all' });

  const body = el('body', {}, [runtimeTag, title, hero, standings, players, unknown]);
  const html = el('html', {}, [body]);
  return { html, body };
}

/* ------------------------------------------------------------- running it */

async function run(source) {
  const { html, body } = buildPage();

  const document = {
    documentElement: html,
    getElementById: (id) => body.find(id),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
  };

  const window = {};
  const sandbox = {
    document,
    window,
    location: { search: '' },
    URLSearchParams,
    Date,
    Math,
    Number,
    String,
    Array,
    console: { warn() {} },
    setInterval: () => 0,
    fetch: () => Promise.resolve({
      status: 200,
      json: () => Promise.resolve({ status: 'success', data: FEED }),
    }),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'overlay-runtime.js' });

  // The first poll() is already in flight; let its promise chain settle.
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 10));

  return { html, body, document };
}

/* --------------------------------------------------------------- the claims */

function check(page) {
  const { body } = page;
  const failures = [];
  const want = (label, actual, expected) => {
    if (actual !== expected) {
      failures.push(`${label}\n      wanted ${JSON.stringify(expected)}\n      got    ${JSON.stringify(actual)}`);
    }
  };

  want('the title is filled', body.find('title').textContent, 'Slot Cup');

  want('a studio picture fills asset.hero at the top level',
    body.find('hero').getAttribute('src'), '/media/studio/hero.png');

  const rows = body.find('standings').children;
  want('a repeat draws one row per team', rows.length, 2);

  if (rows.length === 2) {
    want('a bare name in a row reads that row',
      rows[0].children[1].textContent, 'Test Alpha');
    // This is the one that was silently blank: `asset.bug` inside a repeat
    // fell through to the row and resolved to ''.
    want('a studio picture fills asset.<name> inside a repeat',
      rows[0].children[2].getAttribute('src'), '/media/studio/bug.png');
    want('data-vent-show draws a team that has won',
      rows[0].children[3].style.display, '');
    want('data-vent-show hides a team on zero',
      rows[1].children[3].style.display, 'none');
  }

  const players = body.find('players').children;
  want('a players repeat draws one row per player', players.length, 2);

  if (players.length === 2) {
    want('a player row reads its own field', players[0].children[0].textContent, 'zainab');
    const shots = players[0].children[1].children;
    want('a repeat inside a repeat draws that row own list', shots.length, 2);
    if (shots.length === 2) {
      // The repeat's template is the <img> itself, so each row is one image.
      want('a string row is addressable as url',
        shots[0].getAttribute('src'), '/media/studio/zainab-1.png');
      want('the second picture is the second picture',
        shots[1].getAttribute('src'), '/media/studio/zainab-2.png');
    }
    want('a player with no pictures draws none',
      players[1].children[1].children.length, 0);
  }

  want('a name nobody publishes fills empty rather than throwing',
    body.find('unknown').textContent, '');

  return failures;
}

/* ------------------------------------------------------------------- driver */

const source = fs.readFileSync(RUNTIME, 'utf8');
const selfTest = process.argv.includes('--self-test');

if (!selfTest) {
  const failures = check(await run(source));
  if (failures.length) {
    console.error('overlay runtime: ' + failures.length + ' claim(s) failed\n');
    failures.forEach((f) => console.error('  - ' + f));
    process.exit(1);
  }
  console.log('overlay runtime: every claim holds');
  process.exit(0);
}

// The self-test: three ways of breaking the runtime, each of which must be
// caught. If any of them still passes, this checker is decoration.
// Patterns rather than literals: this file may be checked out with either
// line ending, and a break that silently fails to apply reads as a pass.
const breaks = [
  ['the asset branch removed',
    (s) => s.replace(/\} else if \(parts\[0\] === 'asset'\) \{[\s\S]*?root = data\.asset \|\| \{\}; parts\.shift\(\);/, '} else if (false) {')],
  ['nested repeats not built',
    (s) => s.replace(/\n\s*repeat\(el, row, data\);/, '')],
  ['a marked template element skipped',
    (s) => s.replace(/if \(typeof root\.matches === 'function' && root\.matches\(selector\)\) out\.push\(root\);/, '')],
  ['string rows not addressable',
    (s) => s.replace('return { url: row, img: row, name: row };', 'return {};')],
];

let bad = 0;
for (const [what, breakIt] of breaks) {
  const broken = breakIt(source);
  if (broken === source) {
    console.error('SELF-TEST could not apply the break: ' + what);
    bad += 1;
    continue;
  }
  const failures = check(await run(broken));
  if (failures.length === 0) {
    console.error('SELF-TEST passed a runtime with ' + what + ' - this checker proves nothing');
    bad += 1;
  } else {
    console.log('caught: ' + what + ' (' + failures.length + ' claim(s) failed)');
  }
}

if (bad) process.exit(1);
console.log('self-test: every deliberate break was caught');
