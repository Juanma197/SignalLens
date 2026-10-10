"""SEC filing events as catalyst and risk context: visibility, matching, flags, timing."""
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from app.prototype.events import categorize, event_brief, read_events
from app.prototype.service import stamp
from app.sec_events import SCHEMA

DECISION = datetime(2026, 10, 1, tzinfo=timezone.utc)
SEC = {'security_id': 's1', 'cik': '0000000100'}


@pytest.fixture
def db(tmp_path):
    connection = duckdb.connect(str(tmp_path / 'events.duckdb'))
    connection.execute(SCHEMA)
    yield connection
    connection.close()


def add(db, n, filed, category, *, cik='0000000100', public=None, items='["2.02","9.01"]', document='x-8k.htm'):
    public = public or datetime.combine(filed, datetime.min.time(), timezone.utc) + timedelta(hours=12)
    db.execute("""INSERT INTO sec_event_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?)""",
        [f'e{n}', f'h{n}', 's1', 'SYN.US', cik, f'0000000100-26-{n:06}', '8-K', filed, public, filed, items, document,
         'ref', 'https://data.sec.gov/submissions/CIK0000000100.json', datetime(2026, 9, 30, tzinfo=timezone.utc), '{}', 0.85,
         category, '{}', 'public', 'us', 'synthetic', False])


def test_flags_timing_links_and_visibility(db):
    for n, filed in enumerate((date(2025, 11, 1), date(2026, 2, 1), date(2026, 5, 2), date(2026, 8, 1)), 1):
        add(db, n, filed, 'earnings_financial_results')
    add(db, 10, date(2026, 6, 1), 'capital_raise', items='["3.02","9.01"]')
    add(db, 11, date(2026, 7, 1), 'acquisition_disposal', items='["2.01","9.01"]')
    add(db, 12, date(2026, 9, 1), 'bankruptcy_distress', items='["1.03"]', public=datetime(2026, 10, 2, tzinfo=timezone.utc))  # after cutoff
    add(db, 13, date(2024, 1, 1), 'delisting_compliance', items='["3.01"]')                                                     # outside window
    add(db, 14, date(2026, 7, 2), 'delisting_compliance', items='["3.01"]', cik='0000009999')                                  # other issuer
    found, state = read_events(db, DECISION, stamp)
    brief = event_brief(SEC, found['s1'], DECISION)
    assert state == 'supported' and brief['event_count'] == 6
    assert {f['category'] for f in brief['flags']} == {'capital_raise', 'acquisition_disposal'}
    timing = brief['results_timing']
    assert timing['last_results_filed'] == date(2026, 8, 1) and timing['next_results_estimate'] == date(2026, 8, 1) + timedelta(days=timing['typical_gap_days'])
    assert brief['events'][0]['url'] == 'https://www.sec.gov/Archives/edgar/data/100/000000010026000004/x-8k.htm'
    assert brief['events'][0]['items'] == ['2.02', '9.01']


def test_unsafe_documents_get_folder_links_and_summary_mode_omits_events(db):
    add(db, 1, date(2026, 8, 1), 'material_contract', items='["1.01"]', document='../../evil.htm')
    brief = event_brief(SEC, read_events(db, DECISION, stamp)[0]['s1'], DECISION, full=False)
    assert brief['events'] == [] and brief['counts'] == {'material_contract': 1} and brief['flags'] == []
    full = event_brief(SEC, read_events(db, DECISION, stamp)[0]['s1'], DECISION)
    assert full['events'][0]['url'].endswith('/000000010026000001/')


def test_missing_table_is_reported(tmp_path):
    with duckdb.connect(str(tmp_path / 'empty.duckdb')) as empty:
        assert read_events(empty, DECISION, stamp) == ({}, 'table_absent')


def test_categories_follow_the_8k_items_not_the_stored_label(db):
    """CTVA's 2026 spin-off 8-K/A (items 2.05 and 2.06) was stored as bankruptcy; real bankruptcy is item 1.03."""
    assert categorize(['2.05', '2.06'], 'bankruptcy_distress') == 'impairment'
    assert categorize(['2.05'], 'bankruptcy_distress') == 'restructuring'
    assert categorize(['1.01', '1.03', '9.01'], 'material_contract') == 'bankruptcy_distress'  # with its financing agreement
    assert categorize(['1.01', '3.02'], 'material_contract') == 'material_contract'  # unchanged outside the distress items
    assert categorize([], 'general_company_news') == 'general_company_news'
    add(db, 1, date(2026, 6, 12), 'bankruptcy_distress', items='["2.05"]')
    add(db, 2, date(2026, 7, 1), 'material_contract', items='["1.01","1.03"]')
    brief = event_brief(SEC, read_events(db, DECISION, stamp)[0]['s1'], DECISION)
    assert brief['counts'] == {'restructuring': 1, 'bankruptcy_distress': 1}
    assert [f['category'] for f in brief['flags'] if f['kind'] == 'risk'] == ['bankruptcy_distress']  # restructuring is not a warning sign
    assert brief['events'][1]['label'] == 'Restructuring or exit costs'
