"""Recent SEC filing events (8-K/6-K) as catalyst and risk context.

Reads the stored, already-classified `sec_event_metadata` (no requests). An
event is visible from the later of its public time and retrieval time. Categories
come from the stored classifier, whose confidence is shown. Flags and timing
estimates below are fixed rules and interpretation; they never affect the
shortlist. Each event links to its SEC filing.
"""
from datetime import timedelta
import json
import re

WINDOW_DAYS = 365
MAX_EVENTS = 25
CATEGORY_LABELS = {
    'earnings_financial_results': 'Earnings or financial results',
    'material_contract': 'Material agreement',
    'management_director_change': 'Management or board change',
    'shareholder_matters': 'Shareholder vote or matters',
    'acquisition_disposal': 'Acquisition or disposal',
    'capital_raise': 'Capital raise or new debt',
    'delisting_compliance': 'Listing compliance or delisting',
    'bankruptcy_distress': 'Bankruptcy or financial distress',
    'other_material_event': 'Other material event',
    'material_event_unclassified': 'Material event (unclassified)',
    'general_company_news': 'General company news',
}
# Category -> (kind, explanation) for rule-based flags.
FLAGS = {
    'bankruptcy_distress': ('risk', 'A filing classified as bankruptcy or financial distress appeared in the last year.'),
    'delisting_compliance': ('risk', 'A listing-compliance or delisting notice appeared in the last year.'),
    'capital_raise': ('risk', 'A capital raise or new borrowing was filed in the last year (possible dilution or more debt).'),
    'acquisition_disposal': ('catalyst', 'An acquisition or disposal was filed in the last year; it can change the business mix.'),
}
ACCESSION = re.compile(r'^\d{10}-\d{2}-\d{6}$')
DOCUMENT = re.compile(r'^[A-Za-z0-9._-]{1,120}$')


def read_events(db, decision, stamp, *, known=None):
    """Visible events from the last WINDOW_DAYS, grouped by security_id."""
    tables = {r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    if 'sec_event_metadata' not in tables: return {}, 'table_absent'
    rows = db.execute("""SELECT security_id, cik, accession_number, form, filing_date, public_at, retrieval_at,
            left(item_codes, 200), left(primary_document, 200), event_category, classification_confidence, is_amendment
        FROM sec_event_metadata WHERE filing_date BETWEEN ? AND ?""",
        [decision.date() - timedelta(days=WINDOW_DAYS), decision.date()]).fetchall()
    found = {}
    for sid, cik, accession, form, filed, public, retrieved, items, document, category, confidence, amendment in rows:
        if not (stamp(public) and stamp(retrieved) and stamp(public) <= decision and stamp(retrieved) <= (known or decision)): continue
        try: codes = [str(c) for c in json.loads(items)] if items else []
        except ValueError: codes = []
        found.setdefault(str(sid), []).append({'cik': cik, 'accession': accession, 'form': form, 'filing_date': filed,
            'known_at': max(stamp(public), stamp(retrieved)), 'items': codes, 'category': category,
            'label': CATEGORY_LABELS.get(category, category), 'confidence': confidence, 'amendment': bool(amendment),
            'url': _url(cik, accession, document)})
    return found, 'supported'


def _url(cik, accession, document):
    if not (cik and str(cik).isdigit() and accession and ACCESSION.match(accession)): return None
    base = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}'
    return f'{base}/{document}' if document and DOCUMENT.match(document) else f'{base}/'


def event_brief(sec, events, decision, *, full=True):
    """Events for one company, matched on CIK, with flags and timing estimates."""
    events = sorted([e for e in events if str(e['cik']).lstrip('0') == str(sec.get('cik') or '').lstrip('0')],
                    key=lambda e: (e['filing_date'], e['accession']), reverse=True)
    counts = {}
    for e in events: counts[e['category']] = counts.get(e['category'], 0) + 1
    flags = [{'kind': kind, 'category': cat, 'count': counts[cat], 'text': text}
             for cat, (kind, text) in FLAGS.items() if counts.get(cat)]
    management = counts.get('management_director_change', 0)
    if management >= 3:
        flags.append({'kind': 'risk', 'category': 'management_director_change', 'count': management,
                      'text': f'{management} management or board changes were filed in the last year.'})
    earnings = sorted({e['filing_date'] for e in events if e['category'] == 'earnings_financial_results'})
    timing = None
    if len(earnings) >= 2:
        gaps = sorted((b - a).days for a, b in zip(earnings, earnings[1:]))
        gap = gaps[len(gaps) // 2]
        if 60 <= gap <= 200:
            expected = earnings[-1] + timedelta(days=gap)
            timing = {'last_results_filed': earnings[-1], 'typical_gap_days': gap, 'next_results_estimate': expected,
                      'text': f'Results were last filed {earnings[-1].isoformat()}; at its usual {gap}-day rhythm the next '
                              f'are likely around {expected.isoformat()} (estimate from filing cadence, not an announced date).'}
    latest_known = max((e['known_at'] for e in events), default=None)
    return {'window_days': WINDOW_DAYS, 'event_count': len(events), 'counts': counts, 'flags': flags, 'results_timing': timing,
            'events': events[:MAX_EVENTS] if full else [], 'latest_known_at': latest_known,
            'note': 'Categories come from the stored classifier (confidence shown). Event data is only as recent as its last retrieval.'}
