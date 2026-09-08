"""One answer about a code that has changed hands, for every door.

Lives here rather than in views_door because six endpoints resolve a
ticket by code and all six have to answer the same way. It was in
views_door for an hour and the scanner still said "Not on the list",
because the scanner posts to check_in_ticket and not to the lookup.
"""

def transferred_away(code):
    """What happened to a code that used to work, or None.

    Dead is not the same as unknown, and a door that answers one for the other
    sends away somebody holding a genuine receipt while making the steward
    think they are being lied to. Every endpoint that resolves a ticket by code
    asks this before refusing.

    The LIVE code is deliberately never returned. A steward needs to know the
    code moved and to whom; handing back the working one would turn any of
    these endpoints into a way to convert an old screenshot into a valid pass.
    """
    from .models import TicketTransfer
    record = (TicketTransfer.objects
              .select_related('ticket')
              .filter(old_code=str(code or '').strip().upper())
              .order_by('-at').first())
    if record is None:
        return None
    return {
        'transferred_at': record.at.isoformat(),
        'to_name': record.to_name,
        'now_held_by': record.to_name or record.to_email,
    }
