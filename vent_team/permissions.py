"""What each role in a team is allowed to do.

CEO, 29 August 2026: "no where to also manage the roles of players in the team
and the access they have and what they can control."

The roles existed - owner, captain, vice captain, member, coach, manager,
analyst - and two of them meant something, in one function, written inline:

    if requester_role in ('captain', 'vice_captain'):
        if target_entry.role in ('owner', 'captain', 'vice_captain'):
            return _error('Only the owner can remove captains...')
    elif requester_role != 'owner':
        return _error('You do not have permission to remove members.')

Everywhere else, a role was a label on a member card. A coach and a manager had
exactly the same powers as a member, which is to say none, and nothing said so
anywhere a person could read it.

So the matrix lives here, once, and every view asks it. Two things follow from
putting it in one place: the answer to "what can a manager do" is readable
rather than reconstructed from six views, and a screen can ASK - the front end
reads the same table to decide which buttons to draw, so a member is not shown
a control that will refuse them.
"""

# The permissions themselves. Named for what somebody does, not for the screen
# it happens on, because the same power shows up in more than one place.
INVITE = 'invite'                    # ask a named player to join
MANAGE_LINKS = 'manage_links'        # create or revoke the shareable join link
APPROVE_REQUESTS = 'approve_requests'  # accept or reject somebody asking to join
REMOVE_MEMBER = 'remove_member'      # remove an ordinary member
REMOVE_LEADER = 'remove_leader'      # remove a captain or a vice captain
SET_ROLE = 'set_role'                # change what somebody's role is
EDIT_TEAM = 'edit_team'              # name, logo, banner, description
MANAGE_SETTINGS = 'manage_settings'  # whether the team is open to join
ENTER_TOURNAMENT = 'enter_tournament'  # register the team for something
TRANSFER_OWNERSHIP = 'transfer_ownership'

#: Every permission, for the owner and for building the front end's table.
ALL = (INVITE, MANAGE_LINKS, APPROVE_REQUESTS, REMOVE_MEMBER, REMOVE_LEADER,
       SET_ROLE, EDIT_TEAM, MANAGE_SETTINGS, ENTER_TOURNAMENT,
       TRANSFER_OWNERSHIP)

ROLE_PERMISSIONS = {
    # The owner can do everything, including hand the team to somebody else.
    'owner': set(ALL),

    # A manager runs the team's administration but is not its leader: they can
    # bring people in and take ordinary members out, and they cannot remove a
    # captain, change roles, or give the team away.
    'manager': {INVITE, MANAGE_LINKS, APPROVE_REQUESTS, REMOVE_MEMBER,
                EDIT_TEAM, MANAGE_SETTINGS, ENTER_TOURNAMENT},

    # A captain leads the roster: they bring players in and enter competitions.
    # Removing another captain stays with the owner, so that two captains
    # cannot remove each other.
    'captain': {INVITE, MANAGE_LINKS, APPROVE_REQUESTS, REMOVE_MEMBER,
                ENTER_TOURNAMENT},

    # A vice captain deputises: they can take in the people who ask, and that
    # is deliberately the limit.
    'vice_captain': {APPROVE_REQUESTS, INVITE},

    # A coach and an analyst are part of the team and run none of it.
    'coach': set(),
    'analyst': set(),
    'member': set(),
}

#: What each role is called, and what it can do, in words. Sent to the front
#: end so the role picker explains itself instead of showing seven words with
#: no consequences attached.
ROLE_BLURBS = {
    'owner': 'Runs the team, and the only one who can hand it to somebody else.',
    'manager': 'Handles the admin: invites, the join link, the team page, and removing members.',
    'captain': 'Leads the roster: invites, approves requests, and enters competitions.',
    'vice_captain': 'Deputises: can invite and approve people asking to join.',
    'coach': 'Part of the team. No administrative powers.',
    'analyst': 'Part of the team. No administrative powers.',
    'member': 'Plays for the team.',
}


def permissions_for(role):
    """Everything this role may do. An unknown role can do nothing."""
    return ROLE_PERMISSIONS.get((role or '').lower(), set())


def can(role, permission):
    return permission in permissions_for(role)


def role_table():
    """The whole matrix, for a screen that wants to explain itself.

    The label and the blurb are sent as English AND as a key. A sentence built
    in Python cannot be translated - it arrives already written, and the page
    would show English inside a French screen, which is exactly what happened
    the first time this shipped. The page looks the key up in its own
    dictionary and falls back to the English here, so the server stays the one
    authority on what a role MAY DO and the page stays the one authority on
    what it is CALLED.
    """
    return [
        {
            'role': role,
            'label': role.replace('_', ' ').title(),
            'label_key': 'role.%s' % role,
            'blurb': ROLE_BLURBS.get(role, ''),
            'blurb_key': 'role.%s.blurb' % role,
            'permissions': sorted(perms),
        }
        for role, perms in ROLE_PERMISSIONS.items()
    ]
