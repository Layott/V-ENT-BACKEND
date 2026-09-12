# -*- coding: utf-8 -*-
"""Formations, and where each slot sits on the pitch.

One list, on the server, read by the picker, the validator and the overlay. The
alternative is the frontend holding its own copy, which is the fault this
codebase keeps paying for: two lists that agree until somebody adds a formation
to one of them.

CEO, 4 September 2026: "i know there is a lot more formations than these." There
were eight. EA ships thirty-odd and an organiser running an EAFC league will
have players who use most of them, so this is the full set with their real
names, including the lettered variants (4-4-2 (2), 4-2-3-1 (2)) that are
genuinely different shapes rather than the same one renamed.

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


#: The four back lines every formation is built on, so a back four is in the
#: same place in every shape that has one. A player recognises their own team
#: by its shape, and a defence that moves between formations looks wrong.
_BACK4 = (('LB', 16, 24), ('CB', 38, 20), ('CB', 62, 20), ('RB', 84, 24))
_BACK3 = (('CB', 30, 24), ('CB', 50, 22), ('CB', 70, 24))
_BACK5 = (('LWB', 10, 32), ('CB', 32, 23), ('CB', 50, 21), ('CB', 68, 23),
          ('RWB', 90, 32))
_GK = ('GK', 50, 6)

#: A card is drawn 1.4 times as tall as it is wide, so two slots less than
#: about 14 percent of the pitch apart in y overlap on screen. That is why the
#: middle centre back of a back three and a back five sits at 21 or 22 rather
#: than on the goal line: at 19 it covered the goalkeeper's card. Seen in a
#: Chrome walk on 4 September 2026.


#: Every formation offered, and the position and place of each of its eleven.
#: Positions use EA's own names so a card's position can be compared to a slot
#: without a translation table.
FORMATIONS = {
    # ---------------------------------------------------------- back four
    '4-3-3': _slots(
        _GK, *_BACK4,
        ('CM', 30, 52), ('CM', 50, 48), ('CM', 70, 52),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78)),
    '4-3-3 (2)': _slots(
        _GK, *_BACK4,
        ('CDM', 50, 42), ('CM', 30, 56), ('CM', 70, 56),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78)),
    '4-3-3 (3)': _slots(
        _GK, *_BACK4,
        ('CDM', 34, 44), ('CDM', 66, 44), ('CM', 50, 60),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78)),
    '4-3-3 (4)': _slots(
        _GK, *_BACK4,
        ('CM', 30, 50), ('CAM', 50, 62), ('CM', 70, 50),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78)),
    '4-3-3 (5)': _slots(
        _GK, *_BACK4,
        ('CDM', 50, 42), ('CAM', 34, 60), ('CAM', 66, 60),
        ('LW', 18, 78), ('ST', 50, 86), ('RW', 82, 78)),
    '4-4-2': _slots(
        _GK, *_BACK4,
        ('LM', 14, 54), ('CM', 38, 50), ('CM', 62, 50), ('RM', 86, 54),
        ('ST', 38, 84), ('ST', 62, 84)),
    '4-4-2 (2)': _slots(
        _GK, *_BACK4,
        ('LM', 14, 54), ('CDM', 38, 44), ('CDM', 62, 44), ('RM', 86, 54),
        ('ST', 38, 84), ('ST', 62, 84)),
    '4-4-1-1': _slots(
        _GK, *_BACK4,
        ('LM', 14, 54), ('CM', 38, 50), ('CM', 62, 50), ('RM', 86, 54),
        ('CF', 50, 70), ('ST', 50, 88)),
    '4-2-3-1': _slots(
        _GK, *_BACK4,
        ('CDM', 38, 42), ('CDM', 62, 42),
        ('LM', 16, 66), ('CAM', 50, 64), ('RM', 84, 66),
        ('ST', 50, 88)),
    '4-2-3-1 (2)': _slots(
        _GK, *_BACK4,
        ('CDM', 38, 42), ('CDM', 62, 42),
        ('CAM', 30, 64), ('CAM', 50, 64), ('CAM', 70, 64),
        ('ST', 50, 88)),
    '4-2-2-2': _slots(
        _GK, *_BACK4,
        ('CDM', 38, 42), ('CDM', 62, 42),
        ('CAM', 28, 64), ('CAM', 72, 64),
        ('ST', 38, 86), ('ST', 62, 86)),
    '4-1-2-1-2': _slots(
        _GK, *_BACK4,
        ('CDM', 50, 38),
        ('CM', 26, 56), ('CM', 74, 56),
        ('CAM', 50, 70),
        ('ST', 38, 88), ('ST', 62, 88)),
    '4-1-2-1-2 (2)': _slots(
        _GK, *_BACK4,
        ('CDM', 50, 38),
        ('LM', 12, 58), ('RM', 88, 58),
        ('CAM', 50, 70),
        ('ST', 38, 88), ('ST', 62, 88)),
    '4-1-4-1': _slots(
        _GK, *_BACK4,
        ('CDM', 50, 40),
        ('LM', 14, 60), ('CM', 38, 58), ('CM', 62, 58), ('RM', 86, 60),
        ('ST', 50, 88)),
    '4-3-2-1': _slots(
        _GK, *_BACK4,
        ('CM', 30, 50), ('CM', 50, 46), ('CM', 70, 50),
        ('CF', 34, 72), ('CF', 66, 72),
        ('ST', 50, 90)),
    '4-5-1': _slots(
        _GK, *_BACK4,
        ('LM', 12, 56), ('CM', 34, 52), ('CM', 50, 48), ('CM', 66, 52),
        ('RM', 88, 56),
        ('ST', 50, 88)),
    '4-5-1 (2)': _slots(
        _GK, *_BACK4,
        ('LM', 12, 58), ('CDM', 36, 44), ('CAM', 50, 64), ('CDM', 64, 44),
        ('RM', 88, 58),
        ('ST', 50, 88)),
    '4-2-4': _slots(
        _GK, *_BACK4,
        ('CM', 38, 52), ('CM', 62, 52),
        ('LW', 14, 80), ('ST', 38, 88), ('ST', 62, 88), ('RW', 86, 80)),

    # --------------------------------------------------------- back three
    '3-5-2': _slots(
        _GK, *_BACK3,
        ('LM', 10, 52), ('CM', 34, 50), ('CDM', 50, 42), ('CM', 66, 50),
        ('RM', 90, 52),
        ('ST', 38, 86), ('ST', 62, 86)),
    '3-5-2 (2)': _slots(
        _GK, *_BACK3,
        ('LWB', 10, 46), ('CM', 34, 52), ('CAM', 50, 64), ('CM', 66, 52),
        ('RWB', 90, 46),
        ('ST', 38, 86), ('ST', 62, 86)),
    '3-4-3': _slots(
        _GK, *_BACK3,
        ('LM', 12, 52), ('CM', 38, 50), ('CM', 62, 50), ('RM', 88, 52),
        ('LW', 20, 82), ('ST', 50, 88), ('RW', 80, 82)),
    '3-4-2-1': _slots(
        _GK, *_BACK3,
        ('LM', 12, 52), ('CM', 38, 48), ('CM', 62, 48), ('RM', 88, 52),
        ('CAM', 34, 70), ('CAM', 66, 70),
        ('ST', 50, 90)),
    '3-4-1-2': _slots(
        _GK, *_BACK3,
        ('LM', 12, 52), ('CM', 38, 48), ('CM', 62, 48), ('RM', 88, 52),
        ('CAM', 50, 68),
        ('ST', 38, 88), ('ST', 62, 88)),
    '3-1-4-2': _slots(
        _GK, *_BACK3,
        ('CDM', 50, 38),
        ('LM', 12, 58), ('CM', 38, 56), ('CM', 62, 56), ('RM', 88, 58),
        ('ST', 38, 88), ('ST', 62, 88)),

    # ---------------------------------------------------------- back five
    '5-3-2': _slots(
        _GK, *_BACK5,
        ('CM', 32, 54), ('CM', 50, 50), ('CM', 68, 54),
        ('ST', 38, 86), ('ST', 62, 86)),
    '5-2-1-2': _slots(
        _GK, *_BACK5,
        ('CM', 36, 50), ('CM', 64, 50),
        ('CAM', 50, 68),
        ('ST', 38, 88), ('ST', 62, 88)),
    '5-4-1': _slots(
        _GK, *_BACK5,
        ('LM', 14, 56), ('CM', 38, 52), ('CM', 62, 52), ('RM', 86, 56),
        ('ST', 50, 88)),
    '5-2-2-1': _slots(
        _GK, *_BACK5,
        ('CM', 36, 48), ('CM', 64, 48),
        ('LW', 20, 72), ('RW', 80, 72),
        ('ST', 50, 90)),
}

#: The order they are offered in: back four first because most people play one,
#: then three, then five. A dict is ordered in Python but the picker should not
#: depend on that accident.
FORMATION_KEYS = [
    '4-3-3', '4-3-3 (2)', '4-3-3 (3)', '4-3-3 (4)', '4-3-3 (5)',
    '4-4-2', '4-4-2 (2)', '4-4-1-1',
    '4-2-3-1', '4-2-3-1 (2)', '4-2-2-2',
    '4-1-2-1-2', '4-1-2-1-2 (2)', '4-1-4-1', '4-3-2-1',
    '4-5-1', '4-5-1 (2)', '4-2-4',
    '3-5-2', '3-5-2 (2)', '3-4-3', '3-4-2-1', '3-4-1-2', '3-1-4-2',
    '5-3-2', '5-2-1-2', '5-4-1', '5-2-2-1',
]

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
