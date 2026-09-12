"""How a character battle is decided, written down rather than hidden.

The spec asks the platform to "use an algorithm or system to compare the voted
attributes of the characters and determine a winner". This is that algorithm,
and the one rule it follows is that a reader can check it:

    every attribute is the MEAN of the votes cast on it, to one decimal
    a character's total is the SUM of its five attribute means
    the highest total wins
    equal totals is a TIE, and a tie is an answer

## Why the mean and not the sum of votes

The sum rewards being voted on more, which rewards being popular rather than
strong, and the whole exercise is meant to be about the characters. Two hundred
people scoring Goku's speed at 9 and four people scoring an obscure character's
speed at 10 should leave the obscure character faster.

## Why an attribute with no votes counts as zero and says so

An unvoted attribute is not a strength. Treating it as an average of the others
would invent a number nobody gave, and dropping it from the sum would make a
character with one voted attribute compete with one who has five. So it is zero,
`votes: 0` is published beside it, and the screen can say "nobody has voted on
this" rather than showing a confident 0.0.

## The result is not stored

It is computed from the votes every time it is asked for. A stored winner is a
number that can disagree with the votes underneath it, and the votes are the
truth. `Battle.decided_at` records that voting CLOSED, which is a fact about the
battle rather than a cached answer.
"""
from .catalogue import ATTRIBUTES
from .models import AttributeVote


def score_character(character):
    """One character's attributes, its total, and how many voted.

    Returns a dict shaped for a screen: the numbers it needs and nothing it has
    to compute itself, because a total computed in two places is a total that
    disagrees in one of them.
    """
    rows = AttributeVote.objects.filter(character=character).values_list(
        'attribute', 'score')

    sums = {key: 0 for key in ATTRIBUTES}
    counts = {key: 0 for key in ATTRIBUTES}
    for attribute, score in rows:
        if attribute in sums:
            sums[attribute] += score
            counts[attribute] += 1

    attributes = []
    total = 0.0
    for key, name in ATTRIBUTES.items():
        mean = round(sums[key] / counts[key], 1) if counts[key] else 0.0
        total += mean
        attributes.append({
            'key': key,
            'name': name,
            'score': mean,
            'votes': counts[key],
        })

    return {
        'id': character.character_id,
        'name': character.name,
        'source': character.source,
        'attributes': attributes,
        'total': round(total, 1),
        # The number of PEOPLE who voted on this character at all, which is the
        # honest measure of how well decided it is.
        'voters': AttributeVote.objects.filter(
            character=character).values('user').distinct().count(),
    }


def decide(battle):
    """The whole result: every character scored, and who won.

    `winner` is None while nothing has been voted on, and `tied` is a list when
    two or more share the top total. A tie is a real answer here rather than
    something to break with a rule nobody agreed: these are fictional characters
    and "they are evenly matched" is the correct outcome.
    """
    characters = [score_character(c) for c
                  in battle.characters.filter(is_approved=True)]
    characters.sort(key=lambda c: (-c['total'], c['name']))

    top = characters[0]['total'] if characters else 0
    winners = [c for c in characters if c['total'] == top and top > 0]

    return {
        'characters': characters,
        'winner': winners[0] if len(winners) == 1 else None,
        'tied': [c['name'] for c in winners] if len(winners) > 1 else [],
        'decided': battle.state == 'closed',
        # Said in the payload rather than in a paragraph on the screen, so the
        # rule travels with the numbers and a reader can check the arithmetic.
        'rule': 'mean_per_attribute_summed',
    }
