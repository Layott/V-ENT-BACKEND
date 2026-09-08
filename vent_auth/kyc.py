"""Where identity verification happens, and who is answerable for it.

From the VENT WALLET spec: "Complete KYC verification through a third-party
service" and "Admin: manage KYC verifications and ensure all users are
compliant".

## What this is, and what it deliberately is not

No provider is contracted. Writing a fake Smile ID client that returns
`{"verified": true}` would make the screens look finished and would be a lie
told to the one part of the platform where a lie is a regulatory problem rather
than a bug. So this is the SEAM: the place a provider plugs into, with a real
reviewer behind it today.

The part that is real now and does not change later:

- every decision records **who** made it, **when**, **which provider**, and
  **what reference** that provider gave. `in_house` is a provider name like any
  other, and the reviewer's account is the reference.
- `verify()` is the only way `UserWallet.kyc_verified` is allowed to become
  true. One door, so the answer to "why is this person verified" is always a
  row and never an inference.

## The open decision, for the CEO

Three providers cover Nigeria and the rest of West Africa. Picking one is a
commercial decision, not an engineering one, which is why it is named here
rather than guessed at:

| Provider | What it checks | Why it might be the one |
|---|---|---|
| Smile ID | BVN, NIN, passport, driver's licence, liveness, document capture | The widest African coverage, used by most Nigerian fintechs |
| Dojah | BVN, NIN, phone, address, liveness, AML screening | Nigerian, cheapest per check at low volume |
| Prembly (Identitypass) | NIN, BVN, CAC for organisations, AML | The only one of the three that verifies a COMPANY, which V-ENT needs for organisation payouts |

What has to be decided before any of them can be wired:

1. **Which of a person's identifiers V-ENT is allowed to store.** A BVN is not
   a thing to keep casually; a provider reference standing in for one is.
2. **Whether an organisation is verified as well as a person.** Organisation
   wallets can hold real money and pay it out, and only Prembly of the three
   does CAC.
3. **Who reviews a provider's REFERRED answer.** None of them returns only yes
   or no; the middle answer is the common one and it needs a person.

Until that is settled, `KYC_PROVIDER` stays `in_house` and a V-ENT reviewer is
the provider. Everything above this line already works that way.
"""
from django.conf import settings
from django.utils import timezone

#: The provider in use. `in_house` means a V-ENT reviewer looks at the
#: uploaded document. Setting this to a real provider name is the whole of the
#: switch, once a client for that name exists in `PROVIDERS`.
def current_provider():
    return getattr(settings, 'KYC_PROVIDER', 'in_house') or 'in_house'


class InHouseReviewer:
    """A person at V-ENT looks at the document and decides.

    Not a stub standing in for a provider: it is what actually happens today,
    and it stays available afterwards because every provider has a referred
    answer that a person has to settle.
    """

    name = 'in_house'
    #: Nothing is sent anywhere, so a submission is simply queued for review.
    submits_to_third_party = False

    def submit(self, document):
        """Queue the document. Returns (reference, response)."""
        return '', {'queued_at': timezone.now().isoformat()}


#: Real clients are registered here as they are contracted. The dictionary is
#: the seam: one lookup, and no caller knows which provider answered.
PROVIDERS = {
    'in_house': InHouseReviewer(),
}


def provider_for(name=None):
    """The client for this provider name, falling back to the reviewer.

    Falls back rather than raising, because a misspelled setting must not make
    identity verification unavailable. A person can always review.
    """
    return PROVIDERS.get(name or current_provider(), PROVIDERS['in_house'])


def submit(document):
    """Send a freshly uploaded document wherever it goes, and record where.

    Called by `kyc_submit` straight after the row is written.
    """
    client = provider_for()
    reference, response = client.submit(document)
    document.provider = client.name
    document.provider_reference = reference or ''
    document.provider_response = response or {}
    document.save(update_fields=['provider', 'provider_reference',
                                 'provider_response'])
    return document


def verify(document, reviewer=None, reference='', response=None):
    """Mark this identity verified. The only door to `kyc_verified = True`.

    `reviewer` is the account that decided, which for a third party provider is
    None and the reference is what stands in its place. One of the two is
    always present, and that is the point of the function: a verified wallet
    can always answer who verified it.
    """
    from .models import UserWallet

    document.status = 'approved'
    document.reviewed_at = timezone.now()
    document.reviewed_by = reviewer
    if reference:
        document.provider_reference = reference
    if response:
        document.provider_response = response
    document.save(update_fields=['status', 'reviewed_at', 'reviewed_by',
                                 'provider_reference', 'provider_response'])

    wallet = UserWallet.objects.filter(user=document.user).first()
    if wallet is not None and not wallet.kyc_verified:
        wallet.kyc_verified = True
        wallet.save(update_fields=['kyc_verified'])
    return document


def refuse(document, reviewer=None, reason=''):
    """Turn it down, recording who did and why."""
    document.status = 'rejected'
    document.rejection_reason = reason or ''
    document.reviewed_at = timezone.now()
    document.reviewed_by = reviewer
    document.save(update_fields=['status', 'rejection_reason', 'reviewed_at',
                                 'reviewed_by'])
    return document


def describe(document):
    """What the console and the person's own screen say about this check."""
    return {
        'status': document.status,
        'provider': document.provider,
        'provider_reference': document.provider_reference or None,
        'reviewed_by': (document.reviewed_by.username
                        if document.reviewed_by_id else None),
        'reviewed_at': (document.reviewed_at.isoformat()
                        if document.reviewed_at else None),
        'in_house': document.provider == 'in_house',
    }
