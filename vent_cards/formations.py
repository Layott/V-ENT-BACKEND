# -*- coding: utf-8 -*-
"""Formations, and where each slot sits on the pitch.

One list, on the server, read by the picker, the validator and the overlay. The
alternative is the frontend holding its own copy, which is the fault this
codebase keeps paying for: two lists that agree until somebody adds a formation
to one of them.

A slot's `x` and `y` are percentages of the pitch, with y=0 at the goal line the
side is defending and y=100 at the opposite goal. The overlay and the picker
both draw from these, so a formation looks the same in the console as it does on
air, and adding a formation needs no drawing code at all.

Slot indices are stable and mean the same thing everywhere:

    0-10    the eleven, in the order the formation lists them
    11-17   seven substitutes
    18-22   five reserves

23 in total, which is EA's squad. `slot_index` on `LineupSlot` is checked
against that range, and against the formation for the eleven.
"""

#: How many slots each part of a squad has. The eleven come from the formation.
SUBS = 7
RESERVES = 5
FIRST_SUB = 11
FIRST_RESERVE = FIRST_SUB + SUBS
TOTAL_SLOTS = FIRST_RESERVE + RESERVES          # 23


def _slots(*spec):
    """`('GK', 50, 6)` triples into slot dicts, indexed in order."""
    return [{'index': i, 'position': p, 'x': x, 'y': y}
            for i, (p, x, y) in enumerate(spec)]


#: Every formation offered, and the position and place of each of its eleven.
#: Positions use EA's own names so a card's position can be compared to a slot
#: without a translation table.
FORMATIONS = {
    '4-3-3': _slots(
        ('GK', 50, 6),
        ('LB', 16, 26), ('CB', 38, 22), ('CB', 62, 22), ('RB', 84, 26),
        ('CM', 30, 52), ('CM', 50, 48), ('CM', 70, 52),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78),
    ),
    '4-4-2': _slots(
        ('GK', 50, 6),
        ('LB', 16, 26), ('CB', 38, 22), ('CB', 62, 22), ('RB', 84, 26),
        ('LM', 16, 55), ('CM', 40, 52), ('CM', 60, 52), ('RM', 84, 55),
        ('ST', 40, 84), ('ST', 60, 84),
    ),
    '4-2-3-1': _slots(
        ('GK', 50, 6),
        ('LB', 16, 26), ('CB', 38, 22), ('CB', 62, 22), ('RB', 84, 26),
        ('CDM', 38, 44), ('CDM', 62, 44),
        ('LM', 18, 68), ('CAM', 50, 66), ('RM', 82, 68),
        ('ST', 50, 88),
    ),
    '4-3-2-1': _slots(
        ('GK', 50, 6),
        ('LB', 16, 26), ('CB', 38, 22), ('CB', 62, 22), ('RB', 84, 26),
        ('CM', 30, 50), ('CM', 50, 46), ('CM', 70, 50),
        ('CF', 34, 74), ('CF', 66, 74),
        ('ST', 50, 90),
    ),
    '3-5-2': _slots(
        ('GK', 50, 6),
        ('CB', 30, 22), ('CB', 50, 20), ('CB', 70, 22),
        ('LM', 12, 54), ('CM', 36, 50), ('CDM', 50, 44), ('CM', 64, 50),
        ('RM', 88, 54),
        ('ST', 40, 86), ('ST', 60, 86),
    ),
    '3-4-3': _slots(
        ('GK', 50, 6),
        ('CB', 30, 22), ('CB', 50, 20), ('CB', 70, 22),
        ('LM', 14, 52), ('CM', 40, 50), ('CM', 60, 50), ('RM', 86, 52),
        ('LW', 20, 82), ('ST', 50, 88), ('RW', 80, 82),
    ),
    '5-3-2': _slots(
        ('GK', 50, 6),
        ('LWB', 10, 34), ('CB', 32, 20), ('CB', 50, 18), ('CB', 68, 20),
        ('RWB', 90, 34),
        ('CM', 32, 54), ('CM', 50, 50), ('CM', 68, 54),
        ('ST', 40, 86), ('ST', 60, 86),
    ),
    '4-1-2-1-2': _slots(
        ('GK', 50, 6),
        ('LB', 16, 26), ('CB', 38, 22), ('CB', 62, 22), ('RB', 84, 26),
        ('CDM', 50, 40),
        ('CM', 26, 58), ('CM', 74, 58),
        ('CAM', 50, 70),
        ('ST', 40, 88), ('ST', 60, 88),
    ),
}

#: The order they are offered in. A dict is ordered in Python but the picker
#: should not depend on that accident.
FORMATION_KEYS = ['4-3-3', '4-4-2', '4-2-3-1', '4-3-2-1',
                  '3-5-2', '3-4-3', '5-3-2', '4-1-2-1-2']

DEFAULT_FORMATION = '4-3-3'


def get(key):
    """The slots of one formation, or None."""
    return FORMATIONS.get(str(key or '').strip())


def is_known(key):
    return str(key or '').strip() in FORMATIONS


def slot_position(formation, index):
    """What position slot `index` is in this formation.

    Substitutes and reserves have no position of their own: any card may sit
    on the bench, which is how EA works and how anybody actually picks.
    """
    index = int(index)
    if index >= FIRST_SUB:
        return ''
    slots = get(formation) or []
    for slot in slots:
        if slot['index'] == index:
            return slot['position']
    return ''


def catalogue():
    """What the picker draws its formation list from."""
    return [{
        'key': key,
        'slots': FORMATIONS[key],
        'subs': SUBS,
        'reserves': RESERVES,
        'total_slots': TOTAL_SLOTS,
    } for key in FORMATION_KEYS]
