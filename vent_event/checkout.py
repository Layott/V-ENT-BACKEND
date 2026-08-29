"""What a buyer is asked for, and whether they answered it.

The organiser composes the list. Three fixed columns could not cover it: a
five-a-side needs a shirt size, a conference needs a dietary requirement, a
convention needs to know which day, and none of those is a column anybody could
have guessed in advance.

Two rules hold whatever the organiser composes.

**Email is always collected and always required.** It is not in the list and
cannot be switched off, because a ticket with no way to reach the holder is not
a ticket: no receipt, no re-sending the code when the phone is wiped, and
nothing to attach to an account if they sign up later. Making it optional is the
one setting that would break everything after the sale.

**A refusal names the field.** "Please complete the form" makes somebody hunt
for what they missed, on a phone, at the moment they were about to pay.
"""
import re

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class CheckoutError(ValueError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def clean_field(raw):
    """Check one field the organiser is adding, and return the stored form."""
    if not isinstance(raw, dict):
        raise CheckoutError('A field is a set of named settings.')

    label = str(raw.get('label') or '').strip()
    if not label:
        raise CheckoutError('Give the field a label, so the buyer knows what to '
                            'type.', 'label')

    kind = str(raw.get('kind') or 'text').strip()
    if kind not in {k for k, _label in _KINDS}:
        raise CheckoutError('There is no field of type %r.' % kind, 'kind')

    options = []
    if kind == 'choice':
        options = [str(o).strip() for o in (raw.get('options') or [])
                   if str(o).strip()]
        if len(options) < 2:
            raise CheckoutError('A list needs at least two options to choose '
                                'between.', 'options')

    return {
        'label': label[:80],
        'kind': kind,
        'help_text': str(raw.get('help_text') or '')[:200],
        'required': bool(raw.get('required', False)),
        'options': options,
        'per_ticket': raw.get('per_ticket', True) is not False,
    }


_KINDS = (
    ('text', 'Text'),
    ('phone', 'Phone number'),
    ('number', 'A number'),
    ('choice', 'One of a list'),
    ('checkbox', 'A yes or no'),
)


def catalogue():
    """What an organiser can add, for the editor."""
    return [{'kind': k, 'label': label} for k, label in _KINDS]


def clean_email(raw):
    email = str(raw or '').strip().lower()
    if not email:
        raise CheckoutError('An email address is needed, so the ticket can be '
                            'sent somewhere.', 'email')
    if not EMAIL.match(email):
        raise CheckoutError('That does not look like an email address.', 'email')
    return email[:254]


def answer_for(field, raw):
    """One answer, checked against the field it answers.

    Returns the stored value. Raises `CheckoutError` naming the field when it is
    required and missing, or present and unusable.
    """
    if field.kind == 'checkbox':
        # A checkbox is never "missing": unticked is an answer. A required
        # checkbox means it has to be ticked, which is how a terms box works.
        value = bool(raw)
        if field.required and not value:
            raise CheckoutError('%s has to be ticked.' % field.label, field.id)
        return value

    value = str(raw if raw is not None else '').strip()

    if not value:
        if field.required:
            raise CheckoutError('%s is needed.' % field.label, field.id)
        return ''

    if field.kind == 'number':
        try:
            return int(value)
        except (TypeError, ValueError):
            raise CheckoutError('%s has to be a number.' % field.label, field.id)

    if field.kind == 'choice' and value not in (field.options or []):
        raise CheckoutError('Pick one of the options for %s.' % field.label,
                            field.id)

    if field.kind == 'phone':
        # Deliberately loose. Nigerian numbers are written half a dozen ways
        # and a strict pattern refuses more real numbers than fake ones.
        if len(re.sub(r'\D', '', value)) < 7:
            raise CheckoutError('%s does not look like a phone number.'
                                % field.label, field.id)

    return value[:400]


def collect(event, payload, *, per_ticket_index=None):
    """Every answer for one ticket, or for the order.

    `payload` is what the buyer sent, keyed by field id as a string. Fields the
    organiser has not asked for are ignored rather than stored: a form that
    accepts anything is a form somebody will put a novel into.
    """
    payload = payload or {}
    answers = {}
    for field in event.checkout_fields.all():
        if per_ticket_index is not None and not field.per_ticket:
            continue
        if per_ticket_index is None and field.per_ticket:
            continue
        answers[str(field.id)] = answer_for(field, payload.get(str(field.id)))
    return answers


def describe(event, answers):
    """Answers with their labels, for the door list and the export.

    Stored by field id so a renamed field does not orphan every answer given
    before the rename; labelled here so anybody reading a list sees words.
    """
    by_id = {str(f.id): f for f in event.checkout_fields.all()}
    out = []
    for key, value in (answers or {}).items():
        field = by_id.get(str(key))
        out.append({
            'label': field.label if field else key,
            'value': value,
            'kind': field.kind if field else 'text',
        })
    return out


def phone_from(event, answers):
    """The buyer's number, when the organiser thought to ask for one.

    A phone field is stored with every other answer, keyed by field id. That is
    right for the export, and wrong for everything that needs to ring somebody:
    the door list, the "we can reach you on the day" promise, a cancellation
    notice. Those read `Ticket.attendee_phone` and would find it empty while the
    number sat two layers down in a JSON blob.

    The first phone-kind answer wins. An organiser asking for two numbers is
    asking for a spare, and the first one is the one to try.
    """
    for field in event.checkout_fields.all():
        if field.kind != 'phone':
            continue
        value = str((answers or {}).get(str(field.id)) or '').strip()
        if value:
            return value[:40]
    return ''


def held_by(event, email):
    """How many live tickets this address already holds for this event.

    A cancelled or refunded ticket does not count. Somebody whose order was
    refunded has no ticket, and refusing them a second one because of a record
    that no longer admits anybody is the platform arguing with itself.
    """
    from .models import Ticket

    email = str(email or '').strip().lower()
    if not email:
        return 0
    return Ticket.objects.filter(
        event=event, attendee_email__iexact=email,
    ).exclude(status__in=('cancelled', 'refunded')).count()


def room_for_email(event, email, quantity):
    """Whether this address may take `quantity` more, and why not if it may not.

    Returns `(ok, already, limit)`. `limit` is None when the organiser has set
    no limit, which is the ordinary case.

    CEO: "if a ticket has been sent to an email before, it should not be sent
    again, even if they refresh and type in that same email again."

    So the check is against what the address already HOLDS, not against what
    this request is doing. Refreshing the page and retyping the same address is
    exactly the case it exists to stop, and a per-request check would wave it
    through every time.
    """
    limit = event.max_tickets_per_email
    if not limit:
        return True, 0, None
    already = held_by(event, email)
    return (already + int(quantity or 0)) <= int(limit), already, int(limit)
