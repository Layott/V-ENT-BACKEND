"""Official anime battles: nominate, vote, and read the result.

The result is COMPUTED from the votes every time, by `battles.decide`, and the
payload carries the rule it used. A stored winner is a number that can disagree
with the votes underneath it.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view

from vent_auth.actors import actor_from_request, may_override
from vent_auth.decorators import ROLE_PERMISSIONS

from vent_auth.slugs import resolve_or_redirect

from . import battles, catalogue
from .models import AttributeVote, Battle, BattleCharacter, BattleComment
from .views_series import _err, _ok, _person, _viewer

#: The permission that decides who may run a battle. `moderate_content` rather
#: than a new name: deciding which characters enter and when voting closes is
#: moderation of user submissions, which is what it already means. A permission
#: per feature is how a role table becomes unreadable.
#:
#: Named as a CONSTANT and asserted against the table below, because
#: `may_override` takes the NAME and a name that does not exist silently means
#: "nobody may". Passing `ROLE_PERMISSIONS['moderate_content']` here instead of
#: the string raised `TypeError: unhashable type: 'set'`, which is the loud
#: version of the same mistake and the reason this is written down.
RUNS_A_BATTLE = 'moderate_content'
assert RUNS_A_BATTLE in ROLE_PERMISSIONS


def _need_user(request):
    return actor_from_request(request)


def _find(reference):
    return resolve_or_redirect(reference, entity_type='anime_battle',
                               id_field='battle_id', model=Battle)


def _is_admin(request, user):
    """Whether this caller may run a battle.

    `moderate_content` rather than a new permission: deciding which characters
    enter and when voting closes is moderation of user submissions, which is
    what that permission already names. A permission per feature is how a role
    table becomes unreadable.
    """
    return bool(user and may_override(user, RUNS_A_BATTLE))


def _battle_row(request, battle, viewer=None, deep=False):
    row = {
        'slug': battle.slug,
        'title': battle.title,
        'description': battle.description,
        'state': battle.state,
        'state_label': catalogue.label(catalogue.BATTLE_STATES, battle.state),
        'opens_at': battle.opens_at,
        'closes_at': battle.closes_at,
        'decided_at': battle.decided_at,
        'characters': battle.characters.filter(is_approved=True).count(),
        'created_at': battle.created_at,
    }
    if deep:
        result = battles.decide(battle)
        row.update(result)
        row['may_run'] = _is_admin(request, viewer)
        if viewer is not None and viewer.is_authenticated:
            mine = AttributeVote.objects.filter(
                user=viewer, character__battle=battle).values_list(
                    'character_id', 'attribute', 'score')
            votes = {}
            for character_id, attribute, score in mine:
                votes.setdefault(str(character_id), {})[attribute] = score
            row['my_votes'] = votes
            row['my_nominations'] = [{
                'name': c.name, 'source': c.source, 'approved': c.is_approved,
            } for c in battle.characters.filter(nominated_by=viewer)]
        else:
            row['my_votes'] = {}
            row['my_nominations'] = []
    return row


@api_view(['GET', 'POST'])
def battle_list(request):
    viewer = _viewer(request)

    if request.method == 'POST':
        user, err = _need_user(request)
        if err:
            return err
        if not _is_admin(request, user):
            return _err('That is not yours to do.', 'DO_NOT_PERMISSION_PERFORM',
                        status.HTTP_403_FORBIDDEN)
        title = str(request.data.get('title') or '').strip()
        if not title:
            return _err('Give it a title.', 'TITLE_REQUIRED')
        battle = Battle(title=title[:160],
                        description=str(request.data.get('description') or '')[:4000],
                        created_by=user,
                        opens_at=request.data.get('opens_at') or None,
                        closes_at=request.data.get('closes_at') or None)
        battle.save()
        return _ok(_battle_row(request, battle, user), 'Battle open.',
                   status.HTTP_201_CREATED)

    qs = Battle.objects.all()
    state = request.GET.get('state')
    if state in catalogue.BATTLE_STATES:
        qs = qs.filter(state=state)
    return _ok({'battles': [_battle_row(request, b, viewer) for b in qs[:100]]},
               'Battles.')


@api_view(['GET', 'PATCH'])
def battle_detail(request, reference):
    viewer = _viewer(request)
    battle, moved = _find(reference)
    if moved:
        return _ok({'url': '/anime/battles/%s' % moved}, 'moved')
    if battle is None:
        return _err('No such battle.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        user, err = _need_user(request)
        if err:
            return err
        if not _is_admin(request, user):
            return _err('That is not yours to do.', 'DO_NOT_PERMISSION_PERFORM',
                        status.HTTP_403_FORBIDDEN)
        state = request.data.get('state')
        if state is not None:
            if state not in catalogue.BATTLE_STATES:
                return _err('That is not a state a battle can be in.',
                            'BAD_STATE')
            battle.state = state
            if state == 'closed':
                battle.decided_at = timezone.now()
        for field in ('title', 'description'):
            if field in request.data:
                setattr(battle, field, str(request.data.get(field) or '')[:4000])
        battle.save()

    return _ok(_battle_row(request, battle, viewer, deep=True), 'A battle.')


@api_view(['POST'])
def battle_nominate(request, reference):
    """Suggest a character. An admin decides what enters."""
    user, err = _need_user(request)
    if err:
        return err
    battle, _moved = _find(reference)
    if battle is None:
        return _err('No such battle.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if battle.state != 'nominating':
        return _err('Nominations are closed for that one.',
                    'NOMINATIONS_CLOSED')

    name = str(request.data.get('name') or '').strip()
    if not name:
        return _err('Who?', 'NAME_REQUIRED')
    if BattleCharacter.objects.filter(battle=battle, name__iexact=name).exists():
        return _err('Somebody already nominated them.', 'ALREADY_NOMINATED')

    character = BattleCharacter(
        battle=battle, name=name[:140],
        source=str(request.data.get('source') or '')[:140],
        nominated_by=user,
        # An admin approves. A nomination nobody approved is visible to the
        # person who made it and to nobody else.
        is_approved=bool(_is_admin(request, user)))
    if request.FILES.get('image'):
        character.image = request.FILES['image']
    character.save()
    return _ok({'name': character.name, 'approved': character.is_approved},
               'Nominated.' if not character.is_approved else 'In the battle.',
               status.HTTP_201_CREATED)


@api_view(['POST'])
def battle_approve(request, reference):
    user, err = _need_user(request)
    if err:
        return err
    if not _is_admin(request, user):
        return _err('That is not yours to do.', 'DO_NOT_PERMISSION_PERFORM',
                    status.HTTP_403_FORBIDDEN)
    battle, _moved = _find(reference)
    if battle is None:
        return _err('No such battle.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    character = BattleCharacter.objects.filter(
        battle=battle, name=request.data.get('name')).first()
    if character is None:
        return _err('No such nomination.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)
    character.is_approved = bool(request.data.get('approved', True))
    character.save(update_fields=['is_approved'])
    return _ok({'name': character.name, 'approved': character.is_approved},
               'Decided.')


@api_view(['POST'])
def battle_vote(request, reference):
    """Score one character's attributes. Changeable until voting closes."""
    user, err = _need_user(request)
    if err:
        return err
    battle, _moved = _find(reference)
    if battle is None:
        return _err('No such battle.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)
    if battle.state != 'voting':
        return _err('Voting is not open on that one.', 'VOTING_CLOSED')

    character = BattleCharacter.objects.filter(
        battle=battle, name=request.data.get('character'),
        is_approved=True).first()
    if character is None:
        return _err('That character is not in this battle.', 'NOT_FOUND',
                    status.HTTP_404_NOT_FOUND)

    scores = request.data.get('scores') or {}
    if not isinstance(scores, dict) or not scores:
        return _err('Score at least one attribute.', 'SCORES_REQUIRED')

    for attribute, value in scores.items():
        if attribute not in catalogue.ATTRIBUTES:
            return _err('%s is not an attribute.' % attribute, 'BAD_ATTRIBUTE')
        try:
            score = int(value)
        except (TypeError, ValueError):
            return _err('A score is a number.', 'BAD_SCORE')
        if not catalogue.VOTE_MIN <= score <= catalogue.VOTE_MAX:
            return _err('Scores run from %s to %s.'
                        % (catalogue.VOTE_MIN, catalogue.VOTE_MAX), 'BAD_SCORE')
        AttributeVote.objects.update_or_create(
            character=character, user=user, attribute=attribute,
            defaults={'score': score})

    return _ok(battles.score_character(character), 'Voted.')


@api_view(['GET', 'POST'])
def battle_comments(request, reference):
    viewer = _viewer(request)
    battle, _moved = _find(reference)
    if battle is None:
        return _err('No such battle.', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

    if request.method == 'POST':
        user, err = _need_user(request)
        if err:
            return err
        body = str(request.data.get('body') or '').strip()
        if not body:
            return _err('Say something.', 'BODY_REQUIRED')
        row = BattleComment.objects.create(battle=battle, author=user,
                                           body=body[:4000])
        return _ok({'id': row.comment_id, 'body': row.body,
                    'author': _person(request, user),
                    'created_at': row.created_at}, 'Said.',
                   status.HTTP_201_CREATED)

    rows = BattleComment.objects.filter(
        battle=battle, is_removed=False).select_related('author')
    return _ok({'comments': [{
        'id': r.comment_id,
        'body': r.body,
        'author': _person(request, r.author),
        'created_at': r.created_at,
    } for r in rows]}, 'Comments.')
