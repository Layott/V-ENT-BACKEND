# -*- coding: utf-8 -*-
"""Whether a squad may be submitted, and if not, exactly what is wrong.

CEO, 3 September 2026: "also a place for admins to set rules for the squads
that the players are submitting to use if not they wont be able to submit."

Pure functions, no database access, so the same answer is available to the
submit endpoint, to the organiser's screen and to the player's own preview
without three implementations that eventually disagree.

Every refusal is a CODE plus the numbers behind it, never a sentence built
here. A sentence built in Python cannot be translated, and the player reading
it may be reading in French.
"""


def _cards(slots):
    """The eleven. The bench does not count against a budget or a quota."""
    return [s for s in slots if s.get('slot_index', 99) < 11]


def violations(slots, rules):
    """Everything wrong with this squad, as codes and numbers.

    `rules` is a `SquadRules` row or None. None means the organiser has not
    set any, which is not "anything goes": see `may_submit`.
    """
    if rules is None:
        return [{'code': 'NO_RULES_SET'}]

    eleven = _cards(slots)
    found = []

    if len(eleven) < 11:
        found.append({'code': 'NOT_ELEVEN', 'have': len(eleven), 'need': 11})

    if rules.max_budget_coins:
        spent = sum(int(s.get('price_coins') or 0) for s in eleven)
        if spent > rules.max_budget_coins:
            found.append({'code': 'OVER_BUDGET', 'spent': spent,
                          'allowed': rules.max_budget_coins,
                          'over': spent - rules.max_budget_coins})

    if rules.required_nation and rules.min_from_nation:
        wanted = rules.required_nation.strip().lower()
        have = sum(1 for s in eleven
                   if str(s.get('nation') or '').strip().lower() == wanted)
        if have < rules.min_from_nation:
            found.append({'code': 'NOT_ENOUGH_FROM_NATION',
                          'nation': rules.required_nation,
                          'have': have, 'need': rules.min_from_nation})

    banned = {str(b).strip().lower() for b in (rules.banned_item_types or [])}
    if banned:
        used = sorted({str(s.get('item_type') or '').lower() for s in eleven}
                      & banned)
        if used:
            found.append({'code': 'BANNED_ITEM_TYPE', 'kinds': used})

    if rules.max_card_rating:
        over = sorted(
            {s.get('name') for s in eleven
             if int(s.get('rating') or 0) > rules.max_card_rating})
        if over:
            found.append({'code': 'CARD_TOO_HIGH',
                          'limit': rules.max_card_rating,
                          'cards': over[:6]})

    return found


def may_submit(slots, rules):
    """(allowed, violations). Allowed only when there is nothing wrong."""
    found = violations(slots, rules)
    return (not found), found


def payload(rules):
    """What a player's screen shows so they can build to the rules."""
    if rules is None:
        return None
    return {
        'max_budget_coins': rules.max_budget_coins or None,
        'required_nation': rules.required_nation or None,
        'min_from_nation': rules.min_from_nation or None,
        'banned_item_types': rules.banned_item_types or [],
        'max_card_rating': rules.max_card_rating,
        'notes': rules.notes or '',
    }


def spend(slots):
    """What this eleven costs, for the running total on the picker."""
    return sum(int(s.get('price_coins') or 0) for s in _cards(slots))
