"""Stream elements an organiser can start from instead of uploading.

CEO, 2 September 2026: "pick from existing stream element templates for
tournaments and events."

Two things in this product look similar and are not. The **studio** at
`/studio/<token>/<kind>` is a finished graphic an operator drives from a
console: they type the fixture, press a button, it goes on air. These
**templates** are the other half of the same ask: a complete HTML file the
organiser owns, can open in any editor, can restyle to their own colours, and
which then behaves exactly like anything else they upload. One is for the
organiser who wants it to work now; the other is for the organiser who wants it
to look like theirs.

They are generated rather than kept as thirteen files on disk because the part
that has to be right is the marking, and the marking has to agree with
`views_overlays.TOURNAMENT_NAMES` and `EVENT_NAMES`. Thirteen hand-written
files drift from that list one at a time and each one fails silently, on air.
`tests_overlay_templates.py` asserts every name every template uses is one the
feed sends.

Design: these are composited over live video, so the page paints nothing of its
own and every surface belongs to an element. Filled surfaces and space, never a
1px stroke, and nothing glows, pulses or breathes. A broadcast graphic that
throbs looks cheap at 1080p and worse on a stream at 4500kbps.
"""

# The house palette, as broadcast graphics use it: a dark surface that reads
# over any footage, one committed hue, and white doing most of the work.
INK = '#141416'
SURFACE = '#212225'
RAISED = '#303136'
BRAND = '#ED1C24'
GOOD = '#4caf50'
TEXT = '#FFFFFF'
QUIET = 'rgba(255,255,255,0.62)'

_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
  /* Composited over video: the page paints nothing of its own. */
  html, body { margin: 0; padding: 0; background: transparent; }
  body {
    width: 1920px; height: 1080px; overflow: hidden;
    font-family: "Clash Grotesk", "Helvetica Neue", Arial, sans-serif;
    color: %(text)s; -webkit-font-smoothing: antialiased;
  }
  .stage { position: absolute; inset: 0; }
  /* Motion only for arriving, and only once. Nothing here loops. */
  @keyframes rise { from { opacity: 0; transform: translateY(18px); }
                    to   { opacity: 1; transform: none; } }
  .in { animation: rise .32s cubic-bezier(.2,.7,.3,1) both; }
  @media (prefers-reduced-motion: reduce) { .in { animation: none; } }
%(css)s
</style>
</head>
<body>
<div class="stage">
%(body)s
</div>
</body>
</html>
"""


def _page(title, css, body):
    return _SHELL % {'title': title, 'css': css, 'body': body,
                     'text': TEXT}


# ---------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------

def _scorebar():
    css = """
  .bar { position: absolute; top: 48px; left: 50%%; transform: translateX(-50%%);
         display: flex; align-items: stretch; border-radius: 14px;
         overflow: hidden; box-shadow: 0 10px 28px rgba(0,0,0,.45); }
  .side { background: %(surface)s; display: flex; align-items: center; gap: 20px;
          padding: 18px 34px; min-width: 380px; }
  .side.away { flex-direction: row-reverse; }
  .crest { width: 56px; height: 56px; object-fit: contain; }
  .who { font-size: 34px; font-weight: 700; letter-spacing: .01em; }
  .score { background: %(ink)s; display: flex; align-items: center; gap: 22px;
           padding: 18px 40px; font-size: 44px; font-weight: 800;
           font-variant-numeric: tabular-nums; }
  .rail { height: 6px; background: %(brand)s; }
  .caption { margin: 0 auto; width: max-content; margin-top: 10px;
             background: %(raised)s; border-radius: 8px; padding: 8px 20px;
             font-size: 22px; color: %(quiet)s; }
""" % {'surface': SURFACE, 'ink': INK, 'brand': BRAND, 'raised': RAISED,
       'quiet': QUIET}
    body = """  <div class="bar in">
    <div class="side">
      <img class="crest" src="" data-vent-src="team.logo" alt="">
      <span class="who" data-vent="team.name">ALIEN X</span>
    </div>
    <div class="score">
      <span data-vent="team.points_for">0</span>
      <span data-vent="team.points_against">0</span>
    </div>
    <div class="side away">
      <img class="crest" src="" data-vent-src="tournament.logo" alt="">
      <span class="who" data-vent="tournament.game">EA FC 26</span>
    </div>
  </div>
  <div class="rail"></div>
  <div class="caption" data-vent="tournament.title">Naija Weekly</div>"""
    return _page('Score bar', css, body)


def _standings():
    css = """
  .panel { position: absolute; left: 120px; top: 120px; width: 760px;
           background: %(surface)s; border-radius: 18px; padding: 34px 34px 20px;
           box-shadow: 0 18px 44px rgba(0,0,0,.5); }
  .head { font-size: 40px; font-weight: 800; margin: 0 0 6px; }
  .sub { font-size: 22px; color: %(quiet)s; margin: 0 0 24px; }
  table { width: 100%%; border-collapse: collapse; font-size: 26px; }
  /* Zebra fill rather than rules between rows. */
  tbody tr:nth-child(odd) { background: %(raised)s; }
  td, th { padding: 14px 16px; text-align: left; }
  th { font-size: 18px; color: %(quiet)s; font-weight: 600;
       text-transform: uppercase; letter-spacing: .08em; }
  td:first-child { width: 64px; font-weight: 800; }
  .num { text-align: right; font-variant-numeric: tabular-nums; width: 92px; }
  .crest { width: 34px; height: 34px; object-fit: contain;
           vertical-align: middle; margin-right: 12px; }
""" % {'surface': SURFACE, 'raised': RAISED, 'quiet': QUIET}
    body = """  <div class="panel in">
    <p class="head" data-vent="tournament.title">Naija Weekly</p>
    <p class="sub" data-vent="tournament.game">EA FC 26</p>
    <table>
      <thead><tr><th>#</th><th>Team</th><th class="num">P</th>
        <th class="num">W</th><th class="num">L</th><th class="num">GF</th></tr></thead>
      <tbody data-vent-repeat="standings">
        <tr>
          <td data-vent="place">1</td>
          <td><img class="crest" src="" data-vent-src="logo" alt=""><span data-vent="name">ALIEN X</span></td>
          <td class="num" data-vent="played">6</td>
          <td class="num" data-vent="won">5</td>
          <td class="num" data-vent="lost">1</td>
          <td class="num" data-vent="points_for">18</td>
        </tr>
      </tbody>
    </table>
  </div>"""
    return _page('Standings', css, body)


def _lower_third():
    css = """
  .lt { position: absolute; left: 120px; bottom: 150px; display: flex;
        align-items: stretch; border-radius: 12px; overflow: hidden;
        box-shadow: 0 12px 30px rgba(0,0,0,.45); }
  .flag { width: 14px; background: %(brand)s; }
  .body { background: %(surface)s; padding: 22px 40px; }
  .name { font-size: 52px; font-weight: 800; line-height: 1.05; }
  .role { font-size: 26px; color: %(quiet)s; margin-top: 6px; }
""" % {'brand': BRAND, 'surface': SURFACE, 'quiet': QUIET}
    body = """  <div class="lt in">
    <div class="flag"></div>
    <div class="body">
      <div class="name" data-vent="player.ign">Winlola</div>
      <div class="role" data-vent="team.name">ALIEN X</div>
    </div>
  </div>"""
    return _page('Lower third', css, body)


def _player_card():
    css = """
  .card { position: absolute; right: 140px; top: 160px; width: 520px;
          background: %(surface)s; border-radius: 20px; overflow: hidden;
          box-shadow: 0 18px 44px rgba(0,0,0,.5); }
  .shot { width: 100%%; height: 440px; object-fit: cover; display: block;
          background: %(raised)s; }
  .meta { padding: 26px 30px 30px; }
  .ign { font-size: 46px; font-weight: 800; }
  .id { font-size: 22px; color: %(quiet)s; margin-top: 4px; }
  .team { margin-top: 18px; display: inline-block; background: %(raised)s;
          border-radius: 8px; padding: 8px 16px; font-size: 22px; }
  .stats { display: flex; gap: 12px; margin-top: 22px; }
  .stat { flex: 1; background: %(raised)s; border-radius: 10px; padding: 14px; }
  .k { font-size: 16px; color: %(quiet)s; text-transform: uppercase;
       letter-spacing: .08em; }
  .v { font-size: 34px; font-weight: 800; font-variant-numeric: tabular-nums; }
""" % {'surface': SURFACE, 'raised': RAISED, 'quiet': QUIET}
    body = """  <div class="card in">
    <img class="shot" src="" data-vent-src="player.img" alt="">
    <div class="meta">
      <div class="ign" data-vent="player.ign">Winlola</div>
      <div class="id" data-vent="player.id">1042</div>
      <div class="team" data-vent="team.name">ALIEN X</div>
      <div class="stats">
        <div class="stat"><div class="k">Played</div>
          <div class="v" data-vent="team.played">6</div></div>
        <div class="stat"><div class="k">Won</div>
          <div class="v" data-vent="team.won">5</div></div>
        <div class="stat"><div class="k">Lost</div>
          <div class="v" data-vent="team.lost">1</div></div>
      </div>
    </div>
  </div>"""
    return _page('Player card', css, body)


def _bracket():
    css = """
  .wrap { position: absolute; inset: 90px 120px; background: %(surface)s;
          border-radius: 20px; padding: 40px 44px;
          box-shadow: 0 18px 44px rgba(0,0,0,.5); }
  .head { font-size: 42px; font-weight: 800; margin: 0 0 26px; }
  .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 16px; }
  .seat { background: %(raised)s; border-radius: 12px; padding: 16px 18px;
          display: flex; align-items: center; gap: 14px; min-width: 0; }
  .pos { width: 44px; height: 44px; border-radius: 10px; background: %(ink)s;
         display: grid; place-items: center; font-size: 22px; font-weight: 800;
         flex: none; }
  .nm { font-size: 26px; font-weight: 600; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
  .pts { margin-left: auto; font-size: 26px; font-weight: 800;
         font-variant-numeric: tabular-nums; color: %(good)s; }
""" % {'surface': SURFACE, 'raised': RAISED, 'ink': INK, 'good': GOOD}
    body = """  <div class="wrap in">
    <p class="head" data-vent="tournament.title">Naija Weekly</p>
    <div class="grid" data-vent-repeat="teams">
      <div class="seat">
        <span class="pos" data-vent="place">1</span>
        <span class="nm" data-vent="name">ALIEN X</span>
        <span class="pts" data-vent="won">5</span>
      </div>
    </div>
  </div>"""
    return _page('Bracket', css, body)


def _ticker():
    css = """
  .tk { position: absolute; left: 0; right: 0; bottom: 0; display: flex;
        align-items: stretch; background: %(ink)s; }
  .badge { background: %(brand)s; padding: 20px 34px; font-size: 26px;
           font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
           flex: none; }
  .items { display: flex; align-items: center; gap: 46px; padding: 20px 34px;
           font-size: 26px; overflow: hidden; }
  .item { display: flex; gap: 14px; align-items: baseline; white-space: nowrap; }
  .item .n { font-weight: 700; }
  .item .s { color: %(quiet)s; font-variant-numeric: tabular-nums; }
""" % {'ink': INK, 'brand': BRAND, 'quiet': QUIET}
    body = """  <div class="tk">
    <div class="badge" data-vent="tournament.game">EA FC 26</div>
    <div class="items" data-vent-repeat="standings">
      <div class="item">
        <span class="n" data-vent="name">ALIEN X</span>
        <span class="s" data-vent="won">5</span>
      </div>
    </div>
  </div>"""
    return _page('Ticker', css, body)


def _intro():
    css = """
  .card { position: absolute; left: 140px; bottom: 160px; }
  .kicker { font-size: 26px; letter-spacing: .16em; text-transform: uppercase;
            color: %(quiet)s; }
  .big { font-size: 120px; font-weight: 800; line-height: 1.02;
         margin: 14px 0 20px; max-width: 1100px; }
  .row { display: flex; gap: 14px; }
  .chip { background: %(surface)s; border-radius: 10px; padding: 14px 24px;
          font-size: 28px; }
  .mark { position: absolute; right: 140px; bottom: 160px; width: 220px;
          height: 220px; object-fit: contain; }
""" % {'quiet': QUIET, 'surface': SURFACE}
    body = """  <div class="card in">
    <div class="kicker">Starting soon</div>
    <div class="big" data-vent="tournament.title">Naija Free Fire Weekly</div>
    <div class="row">
      <span class="chip" data-vent="tournament.game">EA FC 26</span>
      <span class="chip" data-vent="tournament.starts_at">Saturday, 7pm</span>
    </div>
  </div>
  <img class="mark" src="" data-vent-src="tournament.logo" alt="">"""
    return _page('Starting soon', css, body)


def _outro():
    css = """
  .card { position: absolute; left: 140px; top: 150px; }
  .kicker { font-size: 26px; letter-spacing: .16em; text-transform: uppercase;
            color: %(quiet)s; }
  .big { font-size: 104px; font-weight: 800; margin: 12px 0 8px; }
  .sub { font-size: 32px; color: %(quiet)s; }
  .final { position: absolute; right: 140px; top: 150px; width: 620px;
           background: %(surface)s; border-radius: 18px; padding: 30px 32px; }
  .final h2 { font-size: 24px; letter-spacing: .1em; text-transform: uppercase;
              color: %(quiet)s; margin: 0 0 18px; font-weight: 600; }
  .line { display: flex; align-items: center; gap: 14px; padding: 12px 14px;
          border-radius: 10px; font-size: 28px; }
  .line:nth-child(odd) { background: %(raised)s; }
  .line .p { width: 40px; font-weight: 800; }
  .line .w { margin-left: auto; font-weight: 800; color: %(good)s;
             font-variant-numeric: tabular-nums; }
""" % {'quiet': QUIET, 'surface': SURFACE, 'raised': RAISED, 'good': GOOD}
    body = """  <div class="card in">
    <div class="kicker">Thanks for watching</div>
    <div class="big" data-vent="tournament.title">Naija Free Fire Weekly</div>
    <div class="sub" data-vent="tournament.game">EA FC 26</div>
  </div>
  <div class="final in">
    <h2>Final table</h2>
    <div data-vent-repeat="standings">
      <div class="line">
        <span class="p" data-vent="place">1</span>
        <span data-vent="name">ALIEN X</span>
        <span class="w" data-vent="won">5</span>
      </div>
    </div>
  </div>"""
    return _page('Thanks for watching', css, body)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _now_next():
    css = """
  .panel { position: absolute; left: 120px; bottom: 140px; width: 900px;
           background: %(surface)s; border-radius: 18px; padding: 34px 38px;
           box-shadow: 0 18px 44px rgba(0,0,0,.5); }
  .venue { font-size: 24px; color: %(quiet)s; }
  .k { font-size: 20px; letter-spacing: .14em; text-transform: uppercase;
       color: %(quiet)s; margin-top: 22px; }
  .now { font-size: 62px; font-weight: 800; line-height: 1.06; }
  .room { display: inline-block; margin-top: 10px; background: %(brand)s;
          border-radius: 8px; padding: 8px 18px; font-size: 24px; }
  .next { margin-top: 26px; background: %(raised)s; border-radius: 12px;
          padding: 20px 24px; }
  .nextTitle { font-size: 34px; font-weight: 700; }
  .nextRoom { font-size: 22px; color: %(quiet)s; margin-top: 4px; }
""" % {'surface': SURFACE, 'quiet': QUIET, 'brand': BRAND, 'raised': RAISED}
    body = """  <div class="panel in">
    <div class="venue" data-vent="event.venue">Alliance Francaise, Ikoyi</div>
    <div class="k">On now</div>
    <div class="now" data-vent="event.now_on">Opening panel</div>
    <span class="room" data-vent="event.room">Main hall</span>
    <div class="next">
      <div class="k" style="margin-top:0">Next</div>
      <div class="nextTitle" data-vent="event.next_on">Cosplay judging</div>
      <div class="nextRoom" data-vent="event.next_room">Studio 2</div>
    </div>
  </div>"""
    return _page('Now and next', css, body)


def _event_lower_third():
    css = """
  .lt { position: absolute; left: 120px; bottom: 150px; display: flex;
        align-items: stretch; border-radius: 12px; overflow: hidden;
        box-shadow: 0 12px 30px rgba(0,0,0,.45); }
  .flag { width: 14px; background: %(brand)s; }
  .body { background: %(surface)s; padding: 22px 40px; }
  .name { font-size: 52px; font-weight: 800; line-height: 1.05; }
  .role { font-size: 26px; color: %(quiet)s; margin-top: 6px; }
""" % {'brand': BRAND, 'surface': SURFACE, 'quiet': QUIET}
    body = """  <div class="lt in">
    <div class="flag"></div>
    <div class="body">
      <div class="name" data-vent="event.now_on">Opening panel</div>
      <div class="role" data-vent="event.room">Main hall</div>
    </div>
  </div>"""
    return _page('Lower third', css, body)


def _programme():
    css = """
  .panel { position: absolute; right: 120px; top: 120px; width: 720px;
           background: %(surface)s; border-radius: 18px; padding: 34px 34px 22px;
           box-shadow: 0 18px 44px rgba(0,0,0,.5); }
  .head { font-size: 38px; font-weight: 800; margin: 0 0 22px; }
  .row { display: flex; gap: 18px; padding: 16px 18px; border-radius: 10px;
         align-items: baseline; }
  .row:nth-child(odd) { background: %(raised)s; }
  .t { font-size: 24px; color: %(quiet)s; font-variant-numeric: tabular-nums;
       width: 150px; flex: none; }
  .ti { font-size: 30px; font-weight: 700; }
  .rm { margin-left: auto; font-size: 22px; color: %(quiet)s; }
""" % {'surface': SURFACE, 'raised': RAISED, 'quiet': QUIET}
    body = """  <div class="panel in">
    <p class="head" data-vent="event.name">Lagos Anime Con</p>
    <div data-vent-repeat="programme">
      <div class="row">
        <span class="t" data-vent="starts_at">11:00</span>
        <span class="ti" data-vent="title">Opening panel</span>
        <span class="rm" data-vent="room">Main hall</span>
      </div>
    </div>
  </div>"""
    return _page('Programme', css, body)


def _sponsor_wall():
    css = """
  .wall { position: absolute; left: 0; right: 0; bottom: 0; background: %(ink)s;
          padding: 26px 60px; display: flex; align-items: center; gap: 54px; }
  .k { font-size: 20px; letter-spacing: .14em; text-transform: uppercase;
       color: %(quiet)s; flex: none; }
  .list { display: flex; align-items: center; gap: 48px; overflow: hidden; }
  .one { display: flex; align-items: center; gap: 16px; white-space: nowrap; }
  .one img { height: 58px; object-fit: contain; }
  .one span { font-size: 26px; font-weight: 600; }
""" % {'ink': INK, 'quiet': QUIET}
    body = """  <div class="wall">
    <div class="k">With thanks to</div>
    <div class="list" data-vent-repeat="sponsors">
      <div class="one">
        <img src="" data-vent-src="logo" alt="">
        <span data-vent="name">Vermillion Encore</span>
      </div>
    </div>
  </div>"""
    return _page('Sponsor wall', css, body)


def _doors():
    css = """
  .card { position: absolute; left: 140px; top: 50%%; transform: translateY(-50%%); }
  .kicker { font-size: 26px; letter-spacing: .16em; text-transform: uppercase;
            color: %(quiet)s; }
  .big { font-size: 118px; font-weight: 800; line-height: 1.02;
         margin: 14px 0 18px; max-width: 1080px; }
  .row { display: flex; gap: 14px; }
  .chip { background: %(surface)s; border-radius: 10px; padding: 14px 24px;
          font-size: 28px; }
  .count { position: absolute; right: 140px; top: 50%%;
           transform: translateY(-50%%); background: %(surface)s;
           border-radius: 20px; padding: 40px 52px; text-align: center; }
  .count .v { font-size: 132px; font-weight: 800; line-height: 1;
              font-variant-numeric: tabular-nums; color: %(good)s; }
  .count .k { font-size: 22px; letter-spacing: .12em; text-transform: uppercase;
              color: %(quiet)s; margin-top: 12px; }
""" % {'quiet': QUIET, 'surface': SURFACE, 'good': GOOD}
    body = """  <div class="card in">
    <div class="kicker">Doors open</div>
    <div class="big" data-vent="event.name">Lagos Anime Con</div>
    <div class="row">
      <span class="chip" data-vent="event.venue">Alliance Francaise, Ikoyi</span>
      <span class="chip" data-vent="event.starts_at">Saturday, 11am</span>
    </div>
  </div>
  <div class="count in">
    <div class="v" data-vent="event.attending">0</div>
    <div class="k">Through the door</div>
  </div>"""
    return _page('Doors open', css, body)


def _event_ticker():
    css = """
  .tk { position: absolute; left: 0; right: 0; bottom: 0; display: flex;
        align-items: stretch; background: %(ink)s; }
  .badge { background: %(brand)s; padding: 20px 34px; font-size: 26px;
           font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
           flex: none; }
  .items { display: flex; align-items: center; gap: 46px; padding: 20px 34px;
           font-size: 26px; overflow: hidden; }
  .item { display: flex; gap: 14px; align-items: baseline; white-space: nowrap; }
  .item .n { font-weight: 700; }
  .item .s { color: %(quiet)s; }
""" % {'ink': INK, 'brand': BRAND, 'quiet': QUIET}
    body = """  <div class="tk">
    <div class="badge" data-vent="event.name">Lagos Anime Con</div>
    <div class="items" data-vent-repeat="programme">
      <div class="item">
        <span class="n" data-vent="title">Opening panel</span>
        <span class="s" data-vent="room">Main hall</span>
      </div>
    </div>
  </div>"""
    return _page('Ticker', css, body)


TOURNAMENT_TEMPLATES = {
    'scorebar': _scorebar,
    'standings': _standings,
    'lower_third': _lower_third,
    'player_card': _player_card,
    'bracket': _bracket,
    'ticker': _ticker,
    'intro': _intro,
    'outro': _outro,
}

EVENT_TEMPLATES = {
    'now_next': _now_next,
    'lower_third': _event_lower_third,
    'programme': _programme,
    'sponsors': _sponsor_wall,
    'ticker': _event_ticker,
    'intro': _doors,
}


def render(kind, key):
    """The complete file for one template, or None if there is no such key."""
    table = EVENT_TEMPLATES if kind == 'event' else TOURNAMENT_TEMPLATES
    build = table.get(key)
    return build() if build else None
