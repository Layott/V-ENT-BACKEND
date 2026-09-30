"""An event's own website, and the embeds that sell its tickets elsewhere (inbox 360).

CEO, 29 September 2026: "we can have ifram embeds available also, we can also
allow people be able to like create their own event pages that looks like a
site or event just use our ticketing software on their own siite, those
options should be available."

Three ways to use V-ENT outside V-ENT, and one checkout behind all of them:

  the website   /events/<slug>/site, the event drawn without the app around
                it, in the organiser's colour, theme, layout and sections
  the embed     /embed/events/<slug>, a frame any site can hold, placed with
                one script tag or a plain iframe
  a button      a link to /events/<slug>?tab=tickets that opens the checkout

Buying always happens on the event page's own checkout, opened in a new tab
from a frame. A second checkout inside the embed would be a second copy of
wallet payment, guest payment, access codes, limits per email, both providers
and every currency, and a copy is the thing that drifts.

This module is the one reader and the one writer of the settings: the
serializer sends `public(event)`, the console posts to `views_site`, and
nothing else touches the columns.
"""
import re

from vent_auth.inputs import BadInput, read_bool, read_choice, read_text

THEMES = ('dark', 'light')
LAYOUTS = ('poster', 'split')

#: Every section the website can draw, in the order it draws them by default.
SECTIONS = ('about', 'tickets', 'schedule', 'venue', 'tournaments', 'sponsors')

_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def sections_of(event):
    """The organiser's order with each section's switch, every section present once.

    A section added to SECTIONS after an organiser saved their list appears,
    switched on, at the end rather than never, and anything stored that is not
    a section is dropped rather than drawn.
    """
    stored = event.site_sections if isinstance(event.site_sections, list) else []
    seen, out = set(), []
    for row in stored:
        if not isinstance(row, dict):
            continue
        key = row.get('key')
        if key in SECTIONS and key not in seen:
            seen.add(key)
            out.append({'key': key, 'visible': bool(row.get('visible', True))})
    out.extend({'key': key, 'visible': True} for key in SECTIONS if key not in seen)
    return out


def public(event):
    """What the event payload carries about its website. Public: it is how the page is drawn."""
    return {
        'enabled': bool(event.site_enabled),
        'headline': event.site_headline or '',
        'accent': event.site_accent or '',
        'theme': event.site_theme if event.site_theme in THEMES else 'dark',
        'layout': event.site_layout if event.site_layout in LAYOUTS else 'poster',
        'sections': sections_of(event),
    }


def _read_sections(raw):
    """A list of {key, visible}; every key a known section, none twice."""
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > len(SECTIONS):
        raise BadInput('sections', 'not a list of sections')
    seen, out = set(), []
    for row in raw:
        if not isinstance(row, dict) or row.get('key') not in SECTIONS or row['key'] in seen:
            raise BadInput('sections', 'not a list of sections')
        seen.add(row['key'])
        visible = row.get('visible', True)
        if not isinstance(visible, bool):
            raise BadInput('sections', 'not a list of sections')
        out.append({'key': row['key'], 'visible': visible})
    return out


def apply(event, data):
    """Write the named fields present in `data`; the rest stay as they were.

    Returns the list of columns changed, for `save(update_fields=...)`.
    Raises BadInput naming the field for anything that cannot be stored.
    """
    changed = []
    if 'enabled' in data:
        event.site_enabled = read_bool(data, 'enabled')
        changed.append('site_enabled')
    if 'headline' in data:
        event.site_headline = read_text(data, 'headline', max_length=120)
        changed.append('site_headline')
    if 'accent' in data:
        accent = read_text(data, 'accent', max_length=7)
        if accent and not _HEX.match(accent):
            raise BadInput('accent', 'not a colour')
        event.site_accent = accent.lower()
        changed.append('site_accent')
    if 'theme' in data:
        event.site_theme = read_choice(data, 'theme', THEMES, default='dark')
        changed.append('site_theme')
    if 'layout' in data:
        event.site_layout = read_choice(data, 'layout', LAYOUTS, default='poster')
        changed.append('site_layout')
    if 'sections' in data:
        event.site_sections = _read_sections(data.get('sections')) or []
        changed.append('site_sections')
    return changed
