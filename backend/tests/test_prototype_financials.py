"""Annual financial-health brief: period selection, visibility, revisions, rules."""
from datetime import date, datetime, timezone

import pytest

from app.prototype.financials import annual_brief, summary_only
from app.prototype.service import finite, stamp

CIK = '0000000100'
SEC = {'security_id': 's1', 'cik': CIK}
DECISION = datetime(2026, 10, 1, tzinfo=timezone.utc)
KNOWN = datetime(2026, 3, 1, tzinfo=timezone.utc)


def fact(concept, value, end, start=None, form='10-K', unit='USD', public=KNOWN, accession='0000000100-26-000001', key=None):
    return {'fact_key': key or f'{concept}-{start}-{end}-{value}-{accession}', 'security_id': 's1', 'cik': CIK, 'taxonomy': 'us-gaap',
            'concept': concept, 'value': value, 'unit': unit, 'period_start': start, 'period_end': end, 'form': form,
            'accession_number': accession, 'public_at': public, 'retrieved_at': public}


def year(y, revenue, op, ni, ocf, capex, shares, concept='Revenues', public=KNOWN, accession='0000000100-26-000001'):
    s, e = date(y, 1, 1), date(y, 12, 31)
    f = lambda c, v, end, start=None, unit='USD': fact(c, v, end, start, unit=unit, public=public, accession=accession)
    return [f(concept, revenue, e, s), f('OperatingIncomeLoss', op, e, s), f('NetIncomeLoss', ni, e, s),
            f('NetCashProvidedByUsedInOperatingActivities', ocf, e, s), f('PaymentsToAcquirePropertyPlantAndEquipment', capex, e, s),
            f('WeightedAverageNumberOfDilutedSharesOutstanding', shares, e, s, unit='shares'),
            f('AssetsCurrent', 300, e), f('LiabilitiesCurrent', 100, e), f('Assets', 1000, e), f('Liabilities', 400, e)]



def brief(rows, decision=DECISION):
    return annual_brief(SEC, rows, decision, stamp=stamp, finite=finite)


def test_annual_series_calculations_and_strengths():
    rows = [r for y, rev in zip(range(2021, 2026), (100, 115, 130, 150, 170)) for r in year(y, rev, rev * 0.2, rev * 0.15, rev * 0.25, rev * 0.05, 1000 - (y - 2021) * 30)]
    b = brief(rows)
    assert [y['fiscal_year_end'] for y in b['years']] == [date(y, 12, 31) for y in range(2021, 2026)]
    last = b['years'][-1]['calculated']
    assert last['operating_margin'] == pytest.approx(0.2) and last['free_cash_flow'] == pytest.approx(170 * 0.2)
    assert last['current_ratio'] == pytest.approx(3.0) and last['liabilities_to_assets'] == pytest.approx(0.4)
    assert last['revenue_growth'] == pytest.approx(170 / 150 - 1)
    kinds = {(o['area'], o['kind']) for o in b['observations']}
    assert {('growth', 'strength'), ('profitability', 'strength'), ('cash generation', 'strength'),
            ('liquidity', 'strength'), ('dilution', 'strength')} <= kinds


def test_only_full_year_10k_periods_and_visible_revisions_are_used():
    rows = year(2024, 100, 10, 8, 12, 2, 50) + year(2025, 120, 12, 9, 14, 3, 50)
    rows += [fact('Revenues', 999, date(2025, 12, 31), date(2025, 10, 1)),          # quarter inside a 10-K
             fact('Revenues', 888, date(2025, 6, 30), date(2025, 1, 1), form='10-Q'),  # 10-Q year-to-date
             fact('Revenues', 777, date(2025, 12, 31), date(2025, 1, 1), unit='EUR'),   # wrong unit
             fact('Revenues', 130, date(2025, 12, 31), date(2025, 1, 1), public=datetime(2026, 6, 1, tzinfo=timezone.utc),
                  accession='0000000100-26-000009')]                                    # later amendment-style revision
    b = brief(rows)
    assert b['years'][-1]['values']['revenue']['value'] == 130
    # Facts known after the cutoff are invisible: the original value is used.
    early = brief(rows, decision=datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert early['years'][-1]['values']['revenue']['value'] == 120


def test_conflicting_values_in_one_revision_are_withheld():
    rows = year(2024, 100, 10, 8, 12, 2, 50) + year(2025, 120, 12, 9, 14, 3, 50)
    rows.append(fact('NetIncomeLoss', 99, date(2025, 12, 31), date(2025, 1, 1), key='conflict'))
    assert 'net_income' not in brief(rows)['years'][-1]['values']


def test_weaknesses_concept_changes_and_gaps():
    rows = year(2023, 100, 5, 4, 3, 10, 100) + year(2024, 90, -5, -6, -2, 10, 110) + year(2025, 80, -8, -9, -4, 10, 125, concept='RevenueFromContractWithCustomerExcludingAssessedTax')
    b = brief(rows)
    text = ' '.join(o['text'] for o in b['observations'])
    assert 'different concepts' in text                       # revenue concept changed: no growth rate
    assert 'revenue_growth' not in b['years'][-1]['calculated']
    assert any(o['kind'] == 'weakness' and o['area'] == 'profitability' for o in b['observations'])
    assert any(o['kind'] == 'weakness' and o['area'] == 'dilution' for o in b['observations'])
    assert brief([])['observations'][0]['kind'] == 'gap'


def test_rates_use_only_consecutive_years():
    rows = year(2019, 10, 1, 1, 1, 0, 1) + year(2023, 100, 10, 8, 12, 2, 100) + year(2024, 110, 11, 9, 13, 2, 100) + year(2025, 121, 12, 10, 14, 2, 100)
    growth = next(o for o in brief(rows)['observations'] if o['area'] == 'growth')
    assert '10.0% a year over 2.0 years' in growth['text'] and '2019-12-31' not in growth['fiscal_years']


def test_summary_only_keeps_observations_without_tables():
    b = brief(year(2024, 100, 10, 8, 12, 2, 50) + year(2025, 120, 12, 9, 14, 3, 50))
    s = summary_only(b)
    assert s['years'] == [] and s['fiscal_years_available'] == 2 and s['observations'] == b['observations']


def test_valuation_multiples_and_not_meaningful_cases():
    from app.prototype.financials import valuation
    b = brief(year(2024, 100, 10, 8, 12, 2, 50) + year(2025, 200, 30, 20, 40, 10, 50) + [fact('StockholdersEquity', -5, date(2025, 12, 31))])
    v = valuation({'market_cap_usd': 600}, b)
    assert v['multiples']['price_to_earnings'] == pytest.approx(30) and v['multiples']['price_to_sales'] == pytest.approx(3)
    assert v['multiples']['price_to_free_cash_flow'] == pytest.approx(20) and 'price_to_book' not in v['multiples']
    assert v['free_cash_flow_yield'] == pytest.approx(0.05) and v['earnings_yield'] == pytest.approx(20 / 600)
    assert "Shareholders' equity is zero or negative" in v['not_meaningful']
    loss = valuation({'market_cap_usd': 600}, brief(year(2025, 100, -10, -8, -12, 2, 50)))
    assert 'price_to_earnings' not in loss['multiples'] and 'Net income is zero or negative' in loss['not_meaningful']
    assert valuation(None, b) is None and valuation({'market_cap_usd': 1}, brief([])) is None


def test_history_uses_first_reported_figures_and_year_end_prices():
    from app.prototype.financials import valuation_history
    rows = [r for y in range(2021, 2026) for r in year(y, 1000, 150, 100, 160, 20, 10, public=datetime(y + 1, 2, 15, tzinfo=timezone.utc), accession=f'0000000100-{y % 100 + 1:02}-000001')]
    # A later filing restates 2021 shares after a 2-for-1 split; history must keep the original.
    rows.append(fact('WeightedAverageNumberOfDilutedSharesOutstanding', 20, date(2021, 12, 31), date(2021, 1, 1), unit='shares',
                     public=datetime(2024, 2, 15, tzinfo=timezone.utc), accession='0000000100-24-000099'))
    first = annual_brief(SEC, rows, DECISION, stamp=stamp, finite=finite, revision='first')
    latest = brief(rows)
    assert first['years'][0]['values']['diluted_shares']['value'] == 10 and latest['years'][0]['values']['diluted_shares']['value'] == 20
    prices = {date(y, 12, 31): {'session': date(y, 12, 31), 'close': 100.0 + (y - 2021) * 50} for y in range(2021, 2026)}
    h = valuation_history(first, prices, current_close=50.0)
    assert [round(y['multiples']['price_to_earnings'], 1) for y in h['years']] == [10.0, 15.0, 20.0, 25.0, 30.0]
    assert h['current']['multiples']['price_to_earnings'] == pytest.approx(5.0)
    pe = next(c for c in h['comparisons'] if c['multiple'] == 'price_to_earnings')
    assert pe['position'] == 'below' and pe['historical_median'] == pytest.approx(20.0) and pe['text'].startswith('P/E 5.0')
    assert valuation_history(first, {}, 50.0)['unavailable'].startswith('No fiscal year has both')


def test_sector_notes_follow_sic_ranges():
    from app.prototype.financials import sector_notes
    assert 'floor-plan' in sector_notes({'sic': 5500})[0]
    assert 'lease-to-own' in sector_notes({'sic': 7359})[0]
    assert sector_notes({'sic': 3560}) == [] and sector_notes(None) == []
