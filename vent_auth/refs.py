"""A team or an organisation named in a request, found the same way everywhere.

Name pickers hand back a slug (inbox 416, 8 October 2026: "anywhere you have
to input a team, player or organization name should also trigger drop-downs").
Three endpoints still looked a team up by its exact name and nothing else:
putting a team straight into a tournament, inviting a team, and challenging a
team to a scrim. A slug picked from the list would have answered "No team by
that name" for a team that exists. The wallets already accepted a slug or a
name; this is that rule, written once.

Order matters: a slug first, because it is what a picker sends and it is
unique; then the name in any case, because people still type names; then an
old numeric id, which earlier screens sent and links may still carry.
"""


def team_by_ref(ref):
    from .models import Teams
    ref = str(ref or '').strip()
    if not ref:
        return None
    team = (Teams.objects.filter(slug=ref).first()
            or Teams.objects.filter(team_name__iexact=ref).first())
    if team is None and ref.isdigit():
        team = Teams.objects.filter(team_id=int(ref)).first()
    return team


def org_by_ref(ref):
    from .models import Organization
    ref = str(ref or '').strip()
    if not ref:
        return None
    org = (Organization.objects.filter(slug=ref).first()
           or Organization.objects.filter(org_name__iexact=ref).first())
    if org is None and ref.isdigit():
        org = Organization.objects.filter(org_id=int(ref)).first()
    return org
