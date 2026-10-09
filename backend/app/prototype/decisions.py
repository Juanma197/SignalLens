"""Monthly decision for every holding: BUY MORE, HOLD, REDUCE, SELL or REVIEW.

Rules are applied in a fixed order and every decision carries its reasons:
no evidence -> REVIEW; broken thesis -> SELL; price well above the middle-case
value -> SELL; price above it -> REDUCE; an oversized position -> REDUCE; clear
upside with a qualifying ranking, no warnings and room in the position -> BUY
MORE; anything else -> HOLD. The wide band between "price above the middle
case" (REDUCE) and "+15% upside" (BUY MORE) is deliberate: small month-to-month
moves leave a holding at HOLD instead of trading it. Decision support only:
nothing is executed and nothing has been validated against later returns.
"""
from .ranking import RULES as RANKING_RULES

RULES = {
    'buy_more_minimum_upside': RANKING_RULES['minimum_upside'],
    'reduce_below_upside': 0.0,
    'sell_below_upside': -0.20,
    'maximum_position_weight_for_buying': 0.25,
    'reduce_above_position_weight': 0.35,
}
DECISIONS = ('SELL', 'REDUCE', 'REVIEW', 'BUY MORE', 'HOLD')


def decide(position, assessment, checks):
    """One holding's decision. `assessment` is its value-ranking entry (or None),
    `checks` its thesis-check evaluation (or None when not covered)."""
    weight = position.get('weight')
    upside = (assessment or {}).get('upside')
    evidence = {'upside': upside, 'weight': weight, 'thesis': (checks or {}).get('overall', 'not_covered'),
                'value_status': (assessment or {}).get('status'), 'conviction': (assessment or {}).get('conviction'),
                'risk': (assessment or {}).get('risk')}
    def result(decision, *reasons):
        return {'decision': decision, 'reasons': [r for r in reasons if r], 'evidence': evidence}
    if checks is None or checks['overall'] == 'not_covered':
        return result('REVIEW', 'SignalLens has no evidence for this holding; decide from your own research.')
    broken = [f'{c["label"]} {"at least" if c["comparator"] == "at_least" else "at most"} {c["threshold"]:g} no longer holds.'
              for c in checks['checks'] if c['status'] == 'broken']
    broken += [a['text'] for a in checks['automatic'] if a['severity'] == 'broken']
    if broken:
        return result('SELL', 'The thesis is broken:', *broken)
    if upside is not None and upside <= RULES['sell_below_upside']:
        return result('SELL', f'The price is {-upside:.0%} above the middle-case value: the discount has been realised and then some.')
    if upside is not None and upside < RULES['reduce_below_upside']:
        return result('REDUCE', f'The price is {-upside:.0%} above the middle-case value; take some profit.')
    if weight is not None and weight > RULES['reduce_above_position_weight']:
        return result('REDUCE', f'The position is {weight:.0%} of the portfolio, above the {RULES["reduce_above_position_weight"]:.0%} limit.')
    warnings = [a['text'] for a in checks['automatic'] if a['severity'] == 'warning']
    unknown = [c['label'] for c in checks['checks'] if c['status'] == 'unknown']
    if upside is None:
        return result('HOLD', 'No valuation range is available, so there is no case for adding.', *warnings)
    blockers = []
    if upside < RULES['buy_more_minimum_upside']:
        blockers.append(f'Upside {upside:.0%} is below the {RULES["buy_more_minimum_upside"]:.0%} needed to add.')
    if assessment.get('status') != 'candidate':
        blockers.append({'watch': 'Conviction is too low or risk too high to add.',
                         'value_trap': 'A value-trap sign blocks adding.'}.get(assessment.get('status'), 'It does not qualify in the ranking.'))
    if warnings: blockers.append('Warnings: ' + ' '.join(warnings))
    if unknown: blockers.append('Some of your conditions cannot be checked: ' + ', '.join(unknown) + '.')
    if weight is not None and weight >= RULES['maximum_position_weight_for_buying']:
        blockers.append(f'The position is already {weight:.0%} of the portfolio.')
    if weight is None: blockers.append('No stored price, so the position size is unknown.')
    if blockers:
        return result('HOLD', 'Thesis intact.' if checks['overall'] == 'intact' else None, *blockers)
    return result('BUY MORE', f'Still undervalued: middle-case upside {upside:.0%}, conviction {assessment["conviction"]}, risk {assessment["risk"]}.',
                  'Thesis intact and the position has room to grow.')
