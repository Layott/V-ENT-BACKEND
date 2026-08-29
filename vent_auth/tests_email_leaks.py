"""Nothing written for us goes out to somebody else.

CEO, 29 August 2026, with a screenshot of a real ticket email:

    {# The scannable code, in the email itself. It used to live only in My
    Tickets, which a guest has no account to reach: their email WAS the ticket
    and it could not be scanned. ... #}

That is a note to whoever edits the template, printed above the QR code in a
customer's inbox, twice in the same message. "That kind of message is not meant
for public and shouldn't be outside."

The cause is a detail of Django's template syntax rather than carelessness:
`{# ... #}` is a SINGLE-LINE comment. The parser does not recognise one that
spans lines, so it is not a comment, it is text, and it renders. Nothing warns.
The template looked correct in an editor, which is why it survived.

So the templates are rendered here and read. Two checks, because they fail
differently:

1. Statically: no `{# ... #}` in any template spans a line break. This catches
   the fault at the source, in a file nobody has to render to test.
2. By rendering: every email template is rendered with a realistic context and
   the output is searched for template syntax, for a Python repr, and for the
   words a developer note starts with. This catches the next variant of the
   same mistake, whatever syntax it arrives in.
"""

import pathlib
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase


TEMPLATES = pathlib.Path(settings.BASE_DIR) / 'vent_auth' / 'templates' / 'emails'

# What must never appear in something a person receives.
LEAKS = [
    ('{#', 'an unrendered template comment'),
    ('#}', 'the end of an unrendered template comment'),
    ('{%', 'an unrendered template tag'),
    ('%}', 'the end of an unrendered template tag'),
    ('{{', 'an unrendered template variable'),
    ('}}', 'the end of an unrendered template variable'),
    ('<QuerySet', 'a Python queryset repr'),
    ('object at 0x', 'a Python object repr'),
    ('TODO', 'a note to ourselves'),
    ('FIXME', 'a note to ourselves'),
    ('XXX', 'a note to ourselves'),
]


class TemplateCommentSyntaxTests(TestCase):
    """The static half: a comment that spans lines is not a comment."""

    def test_no_multiline_hash_comment_in_any_template(self):
        offenders = []
        for path in pathlib.Path(settings.BASE_DIR).rglob('*.html'):
            if 'venv' in path.parts or 'node_modules' in path.parts:
                continue
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'\{#', text):
                close = text.find('#}', match.end())
                line = text[:match.start()].count('\n') + 1
                if close == -1:
                    offenders.append('%s:%d never closed' % (path.name, line))
                elif '\n' in text[match.start():close]:
                    offenders.append('%s:%d spans lines' % (path.name, line))
        self.assertEqual(
            offenders, [],
            "Django's {# #} is single-line. A multi-line one renders as text "
            "and is posted to somebody. Use a comment block tag instead. "
            "Offenders: " + ', '.join(offenders))


class RenderedEmailTests(TestCase):
    """The rendering half: read what actually goes out."""

    #: A context wide enough that no template hits an undefined variable and
    #: silently renders nothing where a sentence should be.
    CONTEXT = {
        'app_url': 'https://v-ent.co',
        'app_host': 'v-ent.co',
        'logo_cid': 'logo',
        'name': 'Winlola',
        'full_name': 'Winlola A',
        'username': 'winlola',
        'email': 'winlola@example.com',
        'code': 'VT-EGRW4KCK',
        'qr_cid': 'qr',
        'title': 'Rivalry Series Season 2',
        'event': {'name': 'Rivalry Series Season 2', 'slug': 'rivalry-series-season-2'},
        'event_name': 'Rivalry Series Season 2',
        'event_slug': 'rivalry-series-season-2',
        'tournament': {'name': 'Rivalry Series', 'slug': 'rivalry-series'},
        'tournament_name': 'Rivalry Series',
        'tier': 'General Admission',
        'session_name': 'Day 1',
        'venue': 'Celebr8 Centre, Ogba, Lagos',
        'doors': '04 Sep 2026, 10:00',
        'paid': '0 VC',
        'amount': '0 VC',
        'quantity': 1,
        'url': 'https://v-ent.co/events/rivalry-series-season-2',
        'link': 'https://v-ent.co/events/rivalry-series-season-2',
        'directions_url': 'https://maps.google.com/?q=Celebr8+Centre',
        'self_check_in_url': 'https://v-ent.co/events/check-in/VT-EGRW4KCK',
        'self_check_in': True,
        'reason': 'The organiser cancelled it.',
        'body': 'Doors open an hour earlier than advertised.',
        'subject': 'A change to your event',
        'message': 'Doors open an hour earlier than advertised.',
        'token': 'abc123',
        'expires_in': '2 hours',
        'starts_at': '04 Sep 2026, 10:00',
        'balance': '0 VC',
        'organiser': 'Vermillion Encore',
    }

    def test_every_email_template_renders_without_leaking_anything(self):
        templates = sorted(p.name for p in TEMPLATES.glob('*.html'))
        self.assertTrue(templates, 'no email templates found at %s' % TEMPLATES)

        problems = []
        for name in templates:
            try:
                html = render_to_string('emails/%s' % name, self.CONTEXT)
            except Exception as exc:                     # noqa: BLE001
                problems.append('%s failed to render: %s' % (name, exc))
                continue
            for needle, why in LEAKS:
                if needle in html:
                    where = html.index(needle)
                    excerpt = html[max(0, where - 40):where + 60].replace('\n', ' ')
                    problems.append('%s leaks %s: ...%s...' % (name, why, excerpt))

        self.assertEqual(problems, [], '\n'.join(problems))

    def test_the_ticket_email_is_the_one_that_was_wrong(self):
        """Named on its own, because this is the message that went out."""
        html = render_to_string('emails/ticket_purchased.html', self.CONTEXT)
        self.assertNotIn('{#', html)
        self.assertNotIn('My Tickets, which a guest has no account to reach', html)
        # And the parts that are supposed to be there still are.
        self.assertIn('VT-EGRW4KCK', html)
