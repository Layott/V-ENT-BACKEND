#!/usr/bin/env python3
"""Two blocks in an email with no space between them.

CEO, 29 September 2026, with a screenshot of the sign-in alert: "even the
emails dont have proper spacing, ensure this is not happening across the
entire webiste also". The paragraph above the Change my password button ended
with `margin:20px 0 0`, and the button carried no space of its own, so the two
touched. Every template that put a button, a code or a rows panel after a
paragraph with no bottom margin had the same fault.

This renders the content block of every email under vent_auth/templates/emails
(twice: once with every optional part present, once with every optional part
absent, so both sides of an {% if %} are read), takes the blocks at the top of
it in order, and measures the space between each neighbouring pair:

    bottom margin of the first + top margin of the second
    + the padding of a spacer cell (a table with no fill whose first cell has
      no fill either; padding inside a filled surface is not space between)

Anything under MIN_GAP pixels is a fault.

    python tools/check-email-spacing.py
    python tools/check-email-spacing.py --self-test
"""
import os
import re
import sys
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAILS = os.path.join(REPO, 'vent_auth', 'templates', 'emails')
MIN_GAP = 12
LABEL_GAP = 6
BLOCKS = {'p', 'table', 'h1', 'h2', 'h3', 'div', 'ul', 'ol', 'img'}
VOID = {'img', 'br', 'hr', 'meta', 'link', 'input'}


def _px(value):
    m = re.match(r'\s*(-?\d+(?:\.\d+)?)px', value or '')
    return float(m.group(1)) if m else 0.0


def _box(style, prop):
    """(top, bottom) of margin or padding from a style attribute."""
    decl = {}
    for part in (style or '').split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            decl[k.strip().lower()] = v.strip()
    top = bottom = 0.0
    if prop in decl:
        vals = decl[prop].split()
        if len(vals) == 1:
            top = bottom = _px(vals[0])
        elif len(vals) in (2, 3):
            top = _px(vals[0])
            bottom = _px(vals[2] if len(vals) == 3 else vals[0])
        elif len(vals) >= 4:
            top, bottom = _px(vals[0]), _px(vals[2])
    if prop + '-top' in decl:
        top = _px(decl[prop + '-top'])
    if prop + '-bottom' in decl:
        bottom = _px(decl[prop + '-bottom'])
    return top, bottom


def _filled(attrs):
    style = (attrs.get('style') or '').lower()
    return bool(attrs.get('bgcolor')) or 'background' in style


class Blocks(HTMLParser):
    """The top-level blocks of a fragment: tag, (top, bottom) outer space, a label."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.blocks = []
        self._cur = None
        self._first_cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth == 0 and tag in BLOCKS:
            top, bottom = _box(attrs.get('style'), 'margin')
            height = re.search(r'(?:^|;)\s*height\s*:\s*(\d+)px', attrs.get('style') or '')
            size = re.search(r'font-size\s*:\s*(\d+)px', attrs.get('style') or '')
            self._cur = {'tag': tag, 'top': top, 'bottom': bottom, 'filled': _filled(attrs), 'text': '',
                         'size': float(size.group(1)) if size else None,
                         'height': float(height.group(1)) if height else None}
            self._first_cell = None
            self.blocks.append(self._cur)
        elif self._cur is not None and self._cur['tag'] == 'table' and tag == 'td' and self._first_cell is None:
            self._first_cell = attrs
            if not self._cur['filled'] and not _filled(attrs):
                pt, pb = _box(attrs.get('style'), 'padding')
                self._cur['top'] += pt
                self._cur['bottom'] += pb
        if tag not in VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        self.depth = max(0, self.depth - 1)
        if self.depth == 0:
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None and len(self._cur['text']) < 50:
            self._cur['text'] = (self._cur['text'] + ' ' + ' '.join(data.split())).strip()[:50]


def gaps(html):
    parser = Blocks()
    parser.feed(html)
    # An empty unfilled div with a height is a spacer (the templates' own
    # `<div style="height:24px">&nbsp;</div>`): it is the space, not a block.
    b, extra = [], 0.0
    for block in parser.blocks:
        if block['tag'] == 'div' and block['height'] is not None and not block['text'] and not block['filled']:
            extra += block['height']
            continue
        block['before'] = extra
        extra = 0.0
        b.append(block)
    out = []
    for first, second in zip(b, b[1:]):
        gap = first['bottom'] + second['before'] + second['top']
        # A label over its value ("Your reserved username", then the name) is
        # meant to sit close: short, small type, and still not touching.
        is_label = (first['tag'] == 'p' and first['size'] is not None and first['size'] <= 14
                    and len(first['text']) <= 40)
        if gap < (LABEL_GAP if is_label else MIN_GAP):
            out.append((gap, '%s "%s"' % (first['tag'], first['text']), '%s "%s"' % (second['tag'], second['text'])))
    return out


def _context(source, present):
    names = set(re.findall(r'{{\s*([a-z_][a-z0-9_]*)', source))
    names |= set(re.findall(r'{%\s*if\s+(?:not\s+)?([a-z_][a-z0-9_]*)', source))
    names |= set(re.findall(r'\bor\s+([a-z_][a-z0-9_]*)', source))
    names |= set(re.findall(r'with\s+[^%]*?=([a-z_][a-z0-9_]*)', source))
    ctx = {n: ('Sample %s' % n if present else '') for n in names}
    ctx.update({
        'rows': [('Label', 'Value'), ('Another', 'Value')],
        'code': '123456', 'name': 'Ada', 'app_url': 'https://v-ent.co',
        'expires_in': '15 minutes',
    })
    return ctx


def rendered():
    """(template, variant, content html) for every email."""
    sys.path.insert(0, REPO)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vent.settings')
    import django
    django.setup()
    from django.template import Context
    from django.template.loader import get_template
    from django.template.loader_tags import BlockNode, ExtendsNode

    for name in sorted(os.listdir(EMAILS)):
        if not name.endswith('.html') or name.startswith('_') or name == 'base.html':
            continue
        source = open(os.path.join(EMAILS, name), encoding='utf-8').read()
        template = get_template('emails/' + name).template
        blocks = [n for n in template.nodelist.get_nodes_by_type(BlockNode) if n.name == 'content']
        if not blocks:
            continue
        for present in (True, False):
            ctx = Context(_context(source, present))
            # An include inside the block needs the render context of a template.
            with ctx.bind_template(template):
                html = blocks[0].nodelist.render(ctx)
            yield name, 'with every part' if present else 'with the optional parts absent', html


def self_test():
    cases = [
        ('the sign-in alert before the fix',
         '<p style="margin:20px 0 0;">If that was you</p>'
         '<table><tr><td bgcolor="#ED1C24" style="border-radius:10px;"><a>Change</a></td></tr></table>', 1),
        ('a spacer cell gives the button its room',
         '<p style="margin:20px 0 0;">If that was you</p>'
         '<table><tr><td style="padding:24px 0 8px;"><table><tr><td bgcolor="#ED1C24"><a>Change</a></td></tr></table></td></tr></table>', 0),
        ('padding inside a filled panel is not space outside it',
         '<p style="margin:0;">Here are the details</p>'
         '<table bgcolor="#26262C"><tr><td style="padding:18px 20px;">Label</td></tr></table>', 1),
        ('a bottom margin on the paragraph is enough',
         '<p style="margin:0 0 16px;">One</p><p style="margin:0;">Two</p>', 0),
        ('longhand margins are read',
         '<p style="margin-bottom:4px;">One</p><p style="margin-top:4px;">Two</p>', 1),
        ('a height spacer div is space',
         '<p style="margin:0;">One</p><div style="height:24px; line-height:24px;">&nbsp;</div><p style="margin:0;">Two</p>', 0),
        ('a filled div with a height is still a block',
         '<p style="margin:0;">One</p><div style="background:#222; height:40px;">&nbsp;</div>', 1),
        ('a small label sits close to its value',
         '<p style="margin:0 0 8px; font-size:13px;">What your profile says now</p><p style="margin:0;">x</p>', 0),
        ('a label that touches its value is still a fault',
         '<p style="margin:0; font-size:13px;">Your reserved username</p><p style="margin:0;">ada</p>', 1),
        ('two real paragraphs 8px apart are too close',
         '<p style="margin:0 0 8px 0;">As a founding member your profile carries your number.</p><p style="margin:0;">Two</p>', 1),
        ('a single block has no neighbour', '<p style="margin:0;">Alone</p>', 0),
    ]
    failed = 0
    for label, html, want in cases:
        got = len(gaps(html))
        ok = got == want
        failed += not ok
        print('%s %s: %d (want %d)' % ('ok  ' if ok else 'FAIL', label, got, want))
    print('%d self-test case(s) pass' % (len(cases) - failed) if not failed else '%d FAILED' % failed)
    return failed == 0


def main():
    if '--self-test' in sys.argv:
        sys.exit(0 if self_test() else 1)
    found = 0
    read = 0
    for name, variant, html in rendered():
        read += 1
        for gap, a, b in gaps(html):
            found += 1
            print('  %s (%s): %gpx between %s and %s' % (name, variant, gap, a, b))
    print('%d email render(s) read, %d place(s) where two blocks touch (under %dpx)' % (read, found, MIN_GAP))
    sys.exit(1 if found else 0)


if __name__ == '__main__':
    main()
