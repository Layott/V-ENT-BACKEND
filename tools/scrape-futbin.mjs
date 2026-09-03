#!/usr/bin/env node
// Read EAFC cards from Futbin and post them to V-ENT.
//
// CEO, 3 September 2026: "then we had to constantly scrape futbin for updates
// and we found a way to scrape all data and info we needed and keep the
// graphics for the cards up to date even... i also need the scraper, a fully
// updated scraper if necessary."
//
// WHY THIS RUNS ON A DESKTOP AND NOT ON THE SERVER
//
// Futbin sits behind Cloudflare. A plain HTTP request answers 403, and so does
// a headless browser from a datacentre address. What works is a REAL Chromium
// window, from a residential or VPN exit, with a profile that keeps the
// clearance cookie between runs. None of that is available to a cron on a VPS,
// so pretending otherwise would produce a feature that passes a test and never
// once works in production.
//
// So this runs where it can, and posts to V-ENT's ingest endpoint. The server
// stays simple and knows nothing about Cloudflare.
//
// SETUP, ONCE
//
//   pnpm add -D playwright        (never npm on this machine)
//   pnpm exec playwright install chromium
//
// RUNNING
//
//   node tools/scrape-futbin.mjs --dry-run          read, print, write nothing
//   node tools/scrape-futbin.mjs --pages 3          the newest three pages
//   node tools/scrape-futbin.mjs                    delta, stops when caught up
//   node tools/scrape-futbin.mjs --full --pages 200 a full sweep
//
//   V_ENT_API=https://api.v-ent.co  CARDS_INGEST_KEY=...  are read from the
//   environment or from a .env beside this file.
//
// WHAT IT TAKES, AND WHAT IT REFUSES TO GUESS
//
// Everything comes from Futbin, including BOTH images: the player's portrait
// and the card frame behind it. A field it cannot read is LEFT OUT of the row
// rather than sent as null, because the server treats an absent field as "do
// not touch" and a null as "clear it". That is the difference between a scrape
// that fails to read a price and a scrape that erases one.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROFILE = path.join(HERE, '.futbin-profile');
const STATE = path.join(HERE, '.futbin-state.json');

const argv = process.argv.slice(2);
const has = (flag) => argv.includes(flag);
const value = (flag, fallback) => {
  const at = argv.indexOf(flag);
  return at >= 0 && argv[at + 1] ? argv[at + 1] : fallback;
};

const DRY_RUN = has('--dry-run');
const FULL = has('--full');
const MAX_PAGES = Number(value('--pages', FULL ? 200 : 12));
const BATCH = 200;

function loadEnv() {
  for (const file of ['.env', '../.env']) {
    const at = path.join(HERE, file);
    if (!fs.existsSync(at)) continue;
    for (const line of fs.readFileSync(at, 'utf8').split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
}
loadEnv();

const API = (process.env.V_ENT_API || 'https://api.v-ent.co').replace(/\/$/, '');
const KEY = process.env.CARDS_INGEST_KEY || '';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
//: Randomised, because a request every 2000ms exactly is a robot announcing
//: itself. This is politeness as much as evasion: Futbin is somebody's site.
const pause = () => 2200 + Math.floor(Math.random() * 1800);

/** "1.2M" and "980K" are coins. "-", "SBC" and "Untradeable" are not prices. */
export function parseCoins(raw) {
  if (raw === null || raw === undefined) return undefined;
  const text = String(raw).trim().replace(/,/g, '');
  if (!text || /^(-|0|sbc|untradeable|n\/a|extinct|nan)$/i.test(text)) return undefined;
  const m = text.match(/^([\d.]+)\s*([KMB])?$/i);
  if (!m) return undefined;
  const scale = { K: 1e3, M: 1e6, B: 1e9 }[(m[2] || '').toUpperCase()] || 1;
  const n = Math.round(parseFloat(m[1]) * scale);
  return Number.isFinite(n) ? n : undefined;
}

/** Futbin's frame file names the variant: `.../cards/tiny/toty.png` -> `toty`. */
export function variantFromFrame(url) {
  const m = String(url || '').match(/\/cards\/(?:[^/]+\/)?([^./?]+)\.(?:png|webp|jpg)/i);
  return m ? m[1].toLowerCase() : '';
}

/** The seven buckets V-ENT stores, from Futbin's much longer variant list. */
export function itemTypeFrom(variant, rating) {
  const v = String(variant || '').toLowerCase();
  if (/icon/.test(v)) return 'icon';
  if (/hero/.test(v)) return 'hero';
  if (v === 'gold' || v === 'gold_rare' || v === 'rare_gold') return 'gold';
  if (/silver/.test(v)) return 'silver';
  if (/bronze/.test(v)) return 'bronze';
  if (v) return 'special';
  // No frame read at all: fall back to the rating bands EA has always used.
  const r = Number(rating) || 0;
  if (r >= 75) return 'gold';
  if (r >= 65) return 'silver';
  return 'bronze';
}

/** Absolute, because Futbin mixes protocol-relative and root-relative URLs. */
export function absolute(url) {
  const s = String(url || '').trim();
  if (!s) return '';
  if (s.startsWith('//')) return 'https:' + s;
  if (s.startsWith('/')) return 'https://www.futbin.com' + s;
  return s;
}

/**
 * Pull every card off one list page.
 *
 * Written against Futbin's current markup and deliberately forgiving: each
 * field is tried through several selectors and left OUT of the row when none
 * of them hits. A scraper that guesses is a scraper that fills a database with
 * confident nonsense.
 */
async function readPage(page) {
  return page.evaluate(() => {
    const text = (el) => (el?.textContent || '').trim();
    const num = (el) => {
      const n = parseInt(text(el).replace(/\D+/g, ''), 10);
      return Number.isFinite(n) ? n : undefined;
    };
    const pick = (root, ...selectors) => {
      for (const s of selectors) {
        const found = root.querySelector(s);
        if (found) return found;
      }
      return null;
    };
    const src = (el) =>
      el?.getAttribute('src') || el?.getAttribute('data-src') ||
      el?.getAttribute('data-original') || '';

    const rows = Array.from(
      document.querySelectorAll('tr.player-row, tr[class*="player-row"]'));
    const out = [];

    for (const row of rows) {
      const link = pick(row, "a[href*='/player/']", 'a.player-row-playercard');
      const href = link?.getAttribute('href') || '';
      const idMatch = href.match(/\/player\/(\d+)/);
      if (!idMatch) continue;

      const name = text(pick(row, 'a.table-player-name', '.table-player-name',
                             "a[href*='/player/']"));
      const rating = num(pick(row, 'td.table-rating .rating-square',
                              '.rating-square', 'td.table-rating'));
      if (!name || !rating) continue;

      const card = { source_id: idMatch[1], name, rating };

      const position = text(pick(row, 'td.table-pos', '.table-player-position',
                                 '.pos'));
      if (position) card.position = position.toUpperCase().slice(0, 8);

      const stat = (...sel) => num(pick(row, ...sel));
      const stats = {
        pac: stat('td.table-pace .table-key-stats', 'td.table-pace'),
        sho: stat('td.table-shooting .table-key-stats', 'td.table-shooting'),
        pas: stat('td.table-passing .table-key-stats', 'td.table-passing'),
        dri: stat('td.table-dribbling .table-key-stats', 'td.table-dribbling'),
        def: stat('td.table-defending .table-key-stats', 'td.table-defending'),
        phy: stat('td.table-physicality .table-key-stats', 'td.table-physicality'),
      };
      const known = Object.fromEntries(
        Object.entries(stats).filter(([, v]) => v !== undefined));
      if (Object.keys(known).length) card.stats = known;

      const ws = stat('td.table-weak-foot', '.table-weak-foot');
      if (ws !== undefined) card.weak_foot = ws;
      const sm = stat('td.table-skills', '.table-skills');
      if (sm !== undefined) card.skill_moves = sm;

      // Price: PlayStation first, then PC, then any price cell. Left OUT when
      // Futbin shows a dash, which means untradeable rather than free.
      const priceEl = pick(row,
        'td.table-price.platform-ps-only .price',
        'td.table-price.platform-pc-only .price',
        'td.table-price .price', 'td.table-price');
      const priceText = text(priceEl);
      if (priceText) card.price_raw = priceText;

      // The portrait.
      const portrait = pick(row,
        ".playercard-26 img[alt]:not([alt=''])",
        '.playercard-26-special-img',
        "img[src*='/img/players/']",
        "img[data-src*='/img/players/']");
      const portraitSrc = src(portrait);
      if (portraitSrc) card.image_raw = portraitSrc;

      // The card frame behind it. Several shapes, then a computed background
      // as the last resort, because silver and bronze rows do not always use
      // the -bg class.
      const frameEl = pick(row,
        'img.playercard-s-26-bg', '.playercard-s-26-bg img',
        "img[src*='/img/cards/']", "img[data-src*='/img/cards/']");
      let frameSrc = src(frameEl);
      if (!frameSrc) {
        const wrap = pick(row, '.playercard-26', '.playercard-s-26',
                          '.playercard-s-26-bg');
        if (wrap) {
          const bg = getComputedStyle(wrap).backgroundImage;
          const m = bg && bg !== 'none' ? bg.match(/url\(["']?([^"')]+)["']?\)/) : null;
          if (m) frameSrc = m[1];
        }
      }
      if (frameSrc) card.frame_raw = frameSrc;

      // Club, league and nation. Futbin exposes the readable names on the
      // icon `title` attributes and the ids in the CDN path, so both are
      // taken: the name for a person, the id for comparing.
      const iconId = (s) => {
        const m = String(s || '').match(
          /\/img\/(?:nation|league|clubs)\/(?:dark\/|light\/)?(\d+)\./i);
        return m ? parseInt(m[1], 10) : undefined;
      };
      const nationImg = pick(row, "img[src*='/img/nation/']",
                             "img[data-src*='/img/nation/']", 'img.nation');
      const clubImg = pick(row, "img[src*='/img/clubs/']",
                           "img[data-src*='/img/clubs/']");
      const leagueImg = pick(row, "img[src*='/img/league/']",
                             "img[data-src*='/img/league/']");

      const title = (el) => (el?.getAttribute('title')
        || el?.getAttribute('alt') || '').trim();
      if (title(nationImg)) card.nation = title(nationImg);
      if (title(clubImg)) card.club = title(clubImg);
      if (title(leagueImg)) card.league = title(leagueImg);
      const nid = iconId(src(nationImg));
      if (nid !== undefined) card.nation_id = nid;

      out.push(card);
    }
    return out;
  });
}

/** Turn one raw row into what the ingest endpoint wants. */
export function toCard(raw) {
  const card = { ...raw };
  const price = parseCoins(raw.price_raw);
  delete card.price_raw;
  if (price !== undefined) card.price_coins = price;

  const image = absolute(raw.image_raw);
  delete card.image_raw;
  if (image) card.image_url = image;

  const frame = absolute(raw.frame_raw);
  delete card.frame_raw;
  if (frame) {
    card.frame_url = frame;
    card.variant = variantFromFrame(frame);
  }
  card.item_type = itemTypeFrom(card.variant, card.rating);
  return card;
}

async function send(cards) {
  if (DRY_RUN) {
    console.log(`  [dry run] would send ${cards.length}`);
    for (const c of cards.slice(0, 3)) console.log('   ', JSON.stringify(c));
    return { added: 0, changed: 0, unchanged: cards.length };
  }
  const res = await fetch(`${API}/cards/ingest/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Cards-Key': KEY },
    body: JSON.stringify({ cards, source: 'futbin' }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.status !== 'success') {
    throw new Error(`ingest refused (${res.status}): ${JSON.stringify(body).slice(0, 300)}`);
  }
  return body.data;
}

async function main() {
  if (!DRY_RUN && !KEY) {
    console.error('CARDS_INGEST_KEY is not set. Use --dry-run to read without writing.');
    process.exit(1);
  }

  const { chromium } = await import('playwright');
  // A persistent profile is the whole trick: the Cloudflare clearance cookie
  // survives between runs, so a scheduled run inside the window is never
  // challenged. Headful because headless is what gets challenged.
  const browser = await chromium.launchPersistentContext(PROFILE, {
    headless: false,
    viewport: { width: 1440, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const page = browser.pages()[0] || (await browser.newPage());

  const totals = { added: 0, changed: 0, unchanged: 0, skipped: 0, read: 0 };
  let quietPages = 0;
  let batch = [];

  try {
    for (let p = 1; p <= MAX_PAGES; p += 1) {
      const url = FULL
        ? `https://www.futbin.com/players?page=${p}`
        : `https://www.futbin.com/latest?page=${p}`;
      process.stdout.write(`page ${p} ... `);
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

      // The Cloudflare gate. It waits for a person rather than failing, which
      // is the only honest thing to do: solving it automatically is both
      // harder and ruder than asking.
      const title = (await page.title()) || '';
      if (/just a moment|attention required|checking your browser/i.test(title)) {
        console.log('\nCloudflare is asking. Solve it in the window, then press Enter here.');
        await new Promise((r) => process.stdin.once('data', r));
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      }

      await page.waitForSelector('tr.player-row, tr[class*="player-row"]', { timeout: 20000 })
        .catch(() => {});

      const rows = await readPage(page);
      totals.read += rows.length;
      console.log(`${rows.length} cards`);
      if (!rows.length) break;

      batch.push(...rows.map(toCard));
      while (batch.length >= BATCH) {
        const chunk = batch.splice(0, BATCH);
        const result = await send(chunk);
        for (const k of ['added', 'changed', 'unchanged', 'skipped']) {
          totals[k] += result[k] || 0;
        }
        // Caught up: two pages in a row where nothing had moved. Only in
        // delta mode, where the list is newest first and there is nothing
        // older worth walking.
        quietPages = (result.added || result.changed) ? 0 : quietPages + 1;
      }

      if (!FULL && quietPages >= 2) {
        console.log('nothing new for two batches; caught up.');
        break;
      }
      await sleep(pause());
    }

    if (batch.length) {
      const result = await send(batch);
      for (const k of ['added', 'changed', 'unchanged', 'skipped']) {
        totals[k] += result[k] || 0;
      }
    }
  } finally {
    await browser.close().catch(() => {});
  }

  console.log(`\nread ${totals.read}: ${totals.added} added, ${totals.changed} changed, `
              + `${totals.unchanged} unchanged, ${totals.skipped} skipped.`);
  if (!DRY_RUN) {
    fs.writeFileSync(STATE, JSON.stringify(
      { lastRunAt: new Date().toISOString(), ...totals }, null, 2));
  }
}

// Importable for the self-test without launching a browser.
if (process.argv[1] && process.argv[1].endsWith('scrape-futbin.mjs')
    && !has('--self-test')) {
  main().catch((err) => { console.error(err.message || err); process.exit(1); });
}

if (has('--self-test')) {
  const cases = [
    ['1.2M is coins', parseCoins('1.2M'), 1200000],
    ['980K is coins', parseCoins('980K'), 980000],
    ['a comma is not a decimal', parseCoins('1,250'), 1250],
    ['a dash is not a price', parseCoins('-'), undefined],
    ['untradeable is not a price', parseCoins('Untradeable'), undefined],
    ['nothing is not zero', parseCoins(''), undefined],
    ['the frame names the variant', variantFromFrame(
      'https://cdn.futbin.com/img/cards/tiny/toty.png'), 'toty'],
    ['icon frames are icons', itemTypeFrom('icon_prime', 91), 'icon'],
    ['hero frames are heroes', itemTypeFrom('hero', 88), 'hero'],
    ['a plain gold frame is gold', itemTypeFrom('gold', 84), 'gold'],
    ['an unknown frame is special', itemTypeFrom('futties_winner', 90), 'special'],
    ['no frame falls back to the rating', itemTypeFrom('', 84), 'gold'],
    ['a low rating with no frame is bronze', itemTypeFrom('', 61), 'bronze'],
    ['protocol-relative urls are made absolute',
      absolute('//cdn.futbin.com/a.png'), 'https://cdn.futbin.com/a.png'],
    ['root-relative urls are made absolute',
      absolute('/img/players/1.png'), 'https://www.futbin.com/img/players/1.png'],
  ];
  let bad = 0;
  for (const [what, got, want] of cases) {
    if (got !== want) { console.error(`SELF-TEST ${what}: got ${got}, wanted ${want}`); bad += 1; }
    else console.log(`ok: ${what}`);
  }

  // A row that could not read its price must not SEND a price, because the
  // server treats a null as "clear it" and an absent field as "leave it".
  const thin = toCard({ source_id: '9', name: 'Nobody', rating: 80, price_raw: '-' });
  if ('price_coins' in thin) { console.error('SELF-TEST an unreadable price was still sent'); bad += 1; }
  else console.log('ok: an unreadable price is left out, not nulled');

  const full = toCard({ source_id: '9', name: 'Somebody', rating: 91,
                        price_raw: '1.2M', image_raw: '//cdn.futbin.com/p.png',
                        frame_raw: '//cdn.futbin.com/img/cards/tiny/toty.png' });
  if (full.price_coins !== 1200000 || !full.image_url || !full.frame_url
      || full.variant !== 'toty' || full.item_type !== 'special') {
    console.error('SELF-TEST a full row came out wrong: ' + JSON.stringify(full));
    bad += 1;
  } else console.log('ok: a full row carries both images and its variant');

  if (bad) process.exit(1);
  console.log('self-test: the parsing holds');
}
