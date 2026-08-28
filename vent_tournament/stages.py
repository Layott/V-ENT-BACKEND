"""Tournaments that run in stages, because that is what real events do.

A major is not one format. It is groups and then a playoff, or Swiss and then a
top cut, or four battle-royale lobbies and then a final. The catalogue already
recorded which formats can feed which - `can_feed_into` on each `Format` - and
nothing read it, so every tournament on the platform was one format from start
to finish and anybody running a real event had to make two tournaments and copy
the names across by hand.

Three ideas here, and only three:

**A stage is a format plus how many leave it.** Everything else about a stage -
its points, its placement table, the order of its tie-breakers - is the existing
ruleset, per stage, because a group phase and a playoff are frequently scored
differently and an organiser who cannot say so has to pick one and be wrong for
half the event.

**A plan is checked before anybody plays.** Whether each stage can feed the next
is knowable at the moment it is composed, and finding out halfway through a group
phase that a round robin cannot feed a battle royale is the version of this that
ends up on a screenshot.

**Advancing is a decision somebody makes, not a thing that happens.** The
organiser presses it when the last match is in. Nothing here watches for a stage
to complete and moves on by itself, because a bracket that reseeds while a
dispute is open is far worse than one that waits.
"""
from . import formats


class StageError(ValueError):
    def __init__(self, message, field=None, index=None):
        super().__init__(message)
        self.field = field
        self.index = index


def clean(raw):
    """Check one proposed stage and return the stored form."""
    if not isinstance(raw, dict):
        raise StageError('A stage is a set of named settings.')

    key = str(raw.get('format') or '').strip()
    fmt = formats.get(key)
    if fmt is None:
        raise StageError('There is no format called %r.' % key, 'format')

    label = str(raw.get('label') or fmt.label).strip()[:60]

    advances = raw.get('advances')
    if advances in ('', None):
        advances = 0
    try:
        advances = int(advances)
    except (TypeError, ValueError):
        raise StageError('How many advance has to be a number.', 'advances')
    if advances < 0:
        raise StageError('How many advance cannot be negative.', 'advances')

    groups = raw.get('groups')
    if groups in ('', None):
        groups = 0
    try:
        groups = int(groups)
    except (TypeError, ValueError):
        raise StageError('How many groups has to be a number.', 'groups')
    if groups < 0 or groups > 64:
        raise StageError('Between 0 and 64 groups. Zero means one field.', 'groups')

    return {
        'format': fmt.key,
        'label': label,
        'advances': advances,
        'groups': groups,
        # The stage's own scoring. Absent means the format's standard rules,
        # which is what most stages want.
        'rules': raw.get('rules') if isinstance(raw.get('rules'), dict) else None,
    }


def plan(raw_stages, *, participants=None):
    """Check a whole plan, in order, and say what is wrong with it.

    Raises `StageError` carrying the index of the offending stage, so the wizard
    can point at the row rather than saying the plan is invalid.
    """
    if not isinstance(raw_stages, list):
        raise StageError('Send the stages as a list, in the order they are played.')
    if not raw_stages:
        return []

    cleaned = []
    for index, item in enumerate(raw_stages):
        try:
            cleaned.append(clean(item))
        except StageError as exc:
            exc.index = index
            raise

    for index, stage in enumerate(cleaned):
        fmt = formats.get(stage['format'])
        last = index == len(cleaned) - 1

        if last:
            # The final stage decides the tournament, so it has nobody to send
            # anywhere. An organiser who set a number here has misunderstood
            # what it means, and silently ignoring it is how they find out at
            # the wrong moment.
            if stage['advances']:
                raise StageError(
                    'The last stage decides the tournament, so nobody advances '
                    'out of it.', 'advances', index)
            continue

        if not stage['advances']:
            raise StageError(
                'Say how many come out of "%s" and into the next stage.'
                % stage['label'], 'advances', index)

        nxt = cleaned[index + 1]
        if fmt.can_feed_into and nxt['format'] not in fmt.can_feed_into:
            allowed = ', '.join(formats.get(k).label for k in fmt.can_feed_into)
            raise StageError(
                '%s cannot feed into %s. It can feed into: %s.'
                % (fmt.label, formats.get(nxt['format']).label, allowed),
                'format', index + 1)
        if not fmt.can_feed_into:
            raise StageError(
                '%s decides a tournament on its own, so nothing can follow it.'
                % fmt.label, 'format', index)

        # A stage cannot send more people on than it will hold, and the stage
        # after it cannot run on fewer than its own minimum.
        nxt_fmt = formats.get(nxt['format'])
        if stage['advances'] < nxt_fmt.min_participants:
            raise StageError(
                '%s needs at least %s, and only %s come out of "%s".'
                % (nxt_fmt.label, nxt_fmt.min_participants,
                   stage['advances'], stage['label']),
                'advances', index)
        if nxt_fmt.even_only and stage['advances'] % 2:
            raise StageError(
                '%s needs an even number, and %s come out of "%s".'
                % (nxt_fmt.label, stage['advances'], stage['label']),
                'advances', index)

    first = formats.get(cleaned[0]['format'])
    if participants is not None and participants < first.min_participants:
        raise StageError(
            '%s needs at least %s entrants and there are %s.'
            % (first.label, first.min_participants, participants),
            'participants', 0)

    return cleaned


def advancing(rows, advances, *, groups=0):
    """Who comes out of a finished stage, in order.

    `rows` is what `tiebreak.standings()` produced. With groups, `advances` is
    read as how many come out of EACH group rather than out of the stage, which
    is what an organiser means when they say "top two from each group" - and
    reading it the other way round produces a playoff of the wrong size without
    anything looking wrong.
    """
    if advances <= 0:
        return []

    if not groups or groups <= 1:
        return list(rows)[:advances]

    by_group = {}
    for row in rows:
        by_group.setdefault(row.get('group') or 0, []).append(row)

    out = []
    for group in sorted(by_group):
        out.extend(by_group[group][:advances])
    return out


def summary(cleaned):
    """A plain sentence per stage, for the wizard and for the guides."""
    lines = []
    for index, stage in enumerate(cleaned):
        fmt = formats.get(stage['format'])
        last = index == len(cleaned) - 1
        where = ('%s groups' % stage['groups']) if stage['groups'] > 1 else 'one field'
        if last:
            lines.append('%s: %s in %s. This decides it.'
                         % (stage['label'], fmt.label, where))
        elif stage['groups'] > 1:
            lines.append('%s: %s in %s, top %s from each group go through.'
                         % (stage['label'], fmt.label, where, stage['advances']))
        else:
            lines.append('%s: %s in %s, top %s go through.'
                         % (stage['label'], fmt.label, where, stage['advances']))
    return lines
