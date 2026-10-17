"""The rating maths, one engine for every rating on V-ENT (P3 W3).

Design section 11 lists "Ratings (Elo, Glicko, TrueSkill)" for ladders, seeding
and McMahon starting scores, and session C's circuit rankings (P5b) rate with
Elo. The formulas live here once, as pure functions; who is rated, from which
matches and over which season stays with each caller (research catalogue 5.2).

Sources:
  * Elo, FIDE rating regulations (2024): expected score from the rating
    difference, change = K x (score - expected); K 40 for a player's first 30
    games and for a junior below 2300, 20 below 2400, 10 from 2400.
  * Glicko-2, Mark Glickman, "Example of the Glicko-2 system" (glicko.net):
    rating, rating deviation and volatility, one rating period at a time.
  * TrueSkill, Microsoft Research: mean and uncertainty; a leaderboard shows
    mean less three standard deviations. The two player update here uses the
    published defaults (25, 25/3, beta 25/6, tau 25/300, draw probability 0.1).
"""
import math
from statistics import NormalDist

_N = NormalDist()


# ---------------------------------------------------------------------------
# Elo
# ---------------------------------------------------------------------------

def elo_expected(ra, rb):
    """What A is expected to score against B: 0.5 between equals."""
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def elo_update(ra, rb, score, k):
    """(new A, new B) after A scored `score` (1 a win, 0.5 a draw, 0 a loss)
    against B: A gains exactly what B loses, K x (score - expected)."""
    delta = k * (score - elo_expected(ra, rb))
    return ra + delta, rb - delta


def fide_k(rating, games, junior=False):
    """FIDE's K factor: 40 for a player's first 30 games, 40 for a junior
    (under 18) rated below 2300, 10 from 2400, otherwise 20."""
    if games < 30 or (junior and rating < 2300):
        return 40
    if rating >= 2400:
        return 10
    return 20


# ---------------------------------------------------------------------------
# Glicko-2
# ---------------------------------------------------------------------------

_SCALE = 173.7178


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi * phi / (math.pi ** 2))


def glicko2_update(rating, rd, volatility, results, tau=0.5):
    """(rating, rd, volatility) after one rating period.

    `results` are (opponent rating, opponent rd, score) for every game in the
    period. With none, only the deviation grows: the longer somebody does not
    play, the less is known about them.
    """
    mu, phi = (rating - 1500) / _SCALE, rd / _SCALE
    if not results:
        return rating, _SCALE * math.sqrt(phi * phi + volatility * volatility), volatility
    rows = []
    for opp_rating, opp_rd, score in results:
        mu_j, phi_j = (opp_rating - 1500) / _SCALE, opp_rd / _SCALE
        g = _g(phi_j)
        expected = 1 / (1 + math.exp(-g * (mu - mu_j)))
        rows.append((g, expected, score))
    v = 1 / sum(g * g * e * (1 - e) for g, e, _s in rows)
    delta = v * sum(g * (s - e) for g, e, s in rows)

    # The new volatility, by the Illinois algorithm (step 5 of the example).
    a = math.log(volatility * volatility)

    def f(x):
        ex = math.exp(x)
        return (ex * (delta * delta - phi * phi - v - ex) / (2 * (phi * phi + v + ex) ** 2)
                - (x - a) / (tau * tau))

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        big_b = a - k * tau
    f_a, f_b = f(big_a), f(big_b)
    while abs(big_b - big_a) > 0.000001:
        big_c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
        f_c = f(big_c)
        if f_c * f_b <= 0:
            big_a, f_a = big_b, f_b
        else:
            f_a /= 2
        big_b, f_b = big_c, f_c
    new_vol = math.exp(big_a / 2)

    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    new_phi = 1 / math.sqrt(1 / (phi_star * phi_star) + 1 / v)
    new_mu = mu + new_phi * new_phi * sum(g * (s - e) for g, e, s in rows)
    return _SCALE * new_mu + 1500, _SCALE * new_phi, new_vol


# ---------------------------------------------------------------------------
# TrueSkill
# ---------------------------------------------------------------------------

def trueskill_1v1(winner, loser, *, draw_probability=0.1, beta=25 / 6, tau=25 / 300):
    """((mu, sigma) of the winner, (mu, sigma) of the loser) after a game with
    no draw. Two new players at 25 / 8.333 become 29.396 / 7.171 and
    20.604 / 7.171 with the published defaults."""
    (mw, sw), (ml, sl) = winner, loser
    sw2, sl2 = sw * sw + tau * tau, sl * sl + tau * tau
    c = math.sqrt(2 * beta * beta + sw2 + sl2)
    margin = _N.inv_cdf((draw_probability + 1) / 2) * math.sqrt(2) * beta
    t, e = (mw - ml) / c, margin / c
    v = _N.pdf(t - e) / _N.cdf(t - e)
    w = v * (v + t - e)
    return ((mw + sw2 / c * v, math.sqrt(sw2 * (1 - sw2 / (c * c) * w))),
            (ml - sl2 / c * v, math.sqrt(sl2 * (1 - sl2 / (c * c) * w))))


def trueskill_conservative(rating):
    """The number a TrueSkill leaderboard sorts by: mean less three sigma."""
    mu, sigma = rating
    return mu - 3 * sigma
