"""An organisation's own Discord server, and exactly what V-ENT may do in it.

CEO, 7 September 2026: "organizers cana dd the bot to their servers and get
people to join their own servers and then use the bot to give people roles, or
send messages to certain channels or tag certain people or roles or create
channels or create categories for them ... including delete messages".

Then, asked whether the bot should ask for all of that at once or let each
organiser choose: **"let each organiser grant only the parts they want."**

This module is that decision made structural.

## Why granular, and why it is not just politeness

Manage Roles, Manage Channels and Manage Messages together are close to
running somebody's server. A server owner handing that to a third party is
trusting V-ENT with their community, and if V-ENT is ever compromised so is
every server that granted it.

An organiser who only wants match announcements should grant only the ability
to post. The all-or-nothing install would have made "I want announcements" and
"I want you to be able to delete my members' messages" the same button.

## Two sources of truth, and Discord is the one that counts

`granted` records what the organiser CHOSE. Discord's live permissions record
what the bot can ACTUALLY do, and a server owner can change those at any time
in Discord without telling us.

So `granted` decides what V-ENT OFFERS, and Discord decides what happens. Every
action checks both, and when they disagree the screen says so rather than
failing with something unhelpful. A stored permission treated as fact is how a
console ends up full of buttons that answer 403.
"""

#: Every capability an organiser can grant, and the Discord permission bits it
#: needs. One definition: the invite URL, the console and the permission check
#: all read this, so a capability cannot exist in one and not the others.
#:
#: `bits` are Discord's own permission flags.
CAPABILITIES = {
    'announce': {
        'label': 'Post announcements',
        'blurb': 'Send match updates, results and reminders into a channel.',
        # View Channels, Send Messages, Embed Links, Read Message History.
        'bits': (1 << 10) | (1 << 11) | (1 << 14) | (1 << 16),
        # Without this the bot can do nothing at all, so it is not optional.
        'required': True,
    },
    'mention': {
        'label': 'Tag people and roles',
        'blurb': 'Mention a role or a person in those announcements, so the '
                 'people who need to see it get a notification.',
        'bits': 1 << 17,                       # Mention Everyone
        'required': False,
    },
    'roles': {
        'label': 'Give and take roles',
        'blurb': 'Give somebody a role when they register or win, and take it '
                 'back afterwards. The bot can only touch roles below its own.',
        'bits': 1 << 28,                       # Manage Roles
        'required': False,
    },
    'channels': {
        'label': 'Create channels and categories',
        'blurb': 'Make a channel per match or a category per tournament, and '
                 'tidy them up afterwards.',
        'bits': 1 << 4,                        # Manage Channels
        'required': False,
    },
    'moderate': {
        'label': 'Delete messages',
        'blurb': 'Remove messages in bulk, or a set number from one person. '
                 'The strongest of these: grant it only if you want V-ENT '
                 'moderating your server.',
        'bits': (1 << 13) | (1 << 15),         # Manage Messages, Attach Files
        'required': False,
    },
}

#: The one capability that is always part of the grant. Kept as a name rather
#: than assumed, so `required` above stays the single place it is decided.
BASE = tuple(k for k, v in CAPABILITIES.items() if v['required'])


def normalise(wanted):
    """The capabilities actually being granted: what was asked for, plus the
    ones that are not optional, minus anything invented."""
    asked = {str(w) for w in (wanted or []) if str(w) in CAPABILITIES}
    return sorted(asked | set(BASE))


def permission_bits(granted):
    """The Discord permissions integer for a set of capabilities.

    This is what goes in the invite URL, so an organiser granting only
    announcements authorises a bot that literally cannot delete a message.
    The restriction is enforced by Discord, not by us remembering.
    """
    total = 0
    for name in normalise(granted):
        total |= CAPABILITIES[name]['bits']
    return total


def missing_for(capability, live_permissions):
    """Which bits this capability needs that the bot does not currently hold.

    `live_permissions` is the integer Discord reports for the bot in that
    server right now. Returns a list of capability-relevant bit names, empty
    when the bot can do it.

    Exists because our record of a grant is a record of INTENT. Somebody can
    remove the bot's role in Discord an hour later, and the honest thing is to
    notice and say so rather than to offer a button that answers 403.
    """
    spec = CAPABILITIES.get(capability)
    if spec is None:
        return ['unknown capability']
    need = spec['bits']
    # Administrator (1 << 3) satisfies everything, which is how most people
    # actually set a bot up.
    if live_permissions & (1 << 3):
        return []
    return [] if (live_permissions & need) == need else [spec['label']]


def catalogue():
    """What the console draws its checkboxes from."""
    return [
        {
            'id': name,
            'label': spec['label'],
            'blurb': spec['blurb'],
            'required': spec['required'],
        }
        for name, spec in CAPABILITIES.items()
    ]
