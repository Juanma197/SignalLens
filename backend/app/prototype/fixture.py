"""Synthetic demonstration only. Creates two NEW files in a NEW temporary folder.

Reuses existing schemas. Never opens an existing database, downloads evidence,
or pretends these invented companies are the operator's actual eligible roster.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb
import pandas as pd

from ..global_universe import SCHEMA_SQL as UNIVERSE_SCHEMA
from ..global_market_data import PRICE_SCHEMA_SQL as MARKET_SCHEMA
from ..sec_ingestion import SCHEMA as SEC_SCHEMA
from ..investment_evidence import SCHEMA as EVIDENCE_SCHEMA
from ..sec_liquidity_ingestion import SCHEMA as LIQUIDITY_SCHEMA

# Only the retained-payload table is needed; it holds SEC submissions documents.
RAW_PROVENANCE_SCHEMA = next(line for line in LIQUIDITY_SCHEMA.splitlines()
                             if line.startswith('CREATE TABLE IF NOT EXISTS sec_liquidity_raw_provenance'))

DECISION = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def insert(db, table, row):
    db.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', list(row.values()))


def insert_many(db, table, rows):
    db.executemany(f'INSERT INTO {table} ({",".join(rows[0])}) VALUES ({",".join("?" for _ in rows[0])})', [list(r.values()) for r in rows])


def create_fixture(folder, count=18):
    folder = Path(folder)
    if not folder.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError('Fixture directory must be beneath the system temporary directory.')
    # exist_ok=False: even an empty existing folder is never reused.
    folder.mkdir(parents=False, exist_ok=False)
    research, production = folder / 'synthetic-research.duckdb', folder / 'synthetic-production.duckdb'
    known = DECISION - timedelta(hours=2)
    # Legacy market/catalogue columns are naive TIMESTAMP holding UTC. Passing an
    # aware value would be converted to the machine's local zone (e.g. BST).
    naive = known.replace(tzinfo=None)
    dates = list(pd.bdate_range(end=known.date(), periods=127).date)
    with duckdb.connect(str(production)) as db:
        db.execute('CREATE TABLE fixture_marker(label VARCHAR)')
        db.execute("INSERT INTO fixture_marker VALUES ('SYNTHETIC ONLY')")
    with duckdb.connect(str(research)) as db:
        db.execute(UNIVERSE_SCHEMA + MARKET_SCHEMA + SEC_SCHEMA + EVIDENCE_SCHEMA + RAW_PROVENANCE_SCHEMA)
        insert(db, 'security_master_retrievals', dict(retrieval_id='fixture', provider='offline', source_label='SYNTHETIC', retrieved_at=naive, status='completed', content_hash='fixture', listing_count=count))
        for i in range(count):
            sid, symbol, issuer = f'synthetic-{i:02}', f'SYN{i:02}.US', f'{i+100:010}'
            insert(db, 'security_listings', dict(retrieval_id='fixture', security_id=sid, company_id=sid, source_key=sid, ticker=f'SYN{i:02}', qualified_symbol=symbol, company_name=f'Synthetic company {i:02}', primary_exchange='US', listing_country='US', currency='USD', instrument_type='common_stock', is_primary=True, active=True, cik=None, first_seen_at=naive, last_seen_at=naive, raw_json='{}'))
            insert(db, 'sec_issuers', dict(security_id=sid, qualified_symbol=symbol, ticker=f'SYN{i:02}', cik=issuer, mapping_source='offline-synthetic', mapped_at=known))
            insert(db, 'security_classification_evidence', dict(evidence_key=f'class-{i}', security_id=sid, qualified_symbol=symbol, security_type='us_operating_company', classification_reason='synthetic', evidence_source_family='offline-synthetic', source_record_identifier=f'class-source-{i}', cik=issuer, durable_identifier=issuer, public_at=known, retrieved_at=known, available_at=known, materialized_at=known, confidence_category='synthetic', review_required=False, provenance='{}', is_current=True))
            insert(db, 'issuer_mapping_candidates', dict(candidate_key=f'map-{i}', security_id=sid, qualified_symbol=symbol, cik=issuer, evidence_source='offline-synthetic-reviewed', source_identifier=f'identity-{i}', effective_from=datetime(2020,1,1,tzinfo=timezone.utc), confidence_category='synthetic', review_status='approved', conflict_state='none', ticker_reuse_protected=True, observed_at=known))
            insert_many(db, 'global_price_observations', [dict(qualified_symbol=symbol, trading_date=d, exchange='US', currency='USD', open=value, high=value+1, low=value-1, close=value, adjusted_close=value, volume=10000, status='available', source='offline-synthetic', retrieved_at=naive)
                for j, d in enumerate(dates) for value in [100 * (1 + (i-3)*0.002*j/126)]])
            insert(db, 'corporate_action_coverage_evidence', dict(evidence_key=f'action-{i}', security_id=sid, qualified_symbol=symbol, coverage_state='verified_no_action', assessed_from=dates[0], assessed_to=dates[-1], source_identifier='offline-synthetic-checkpoint', public_at=known, retrieved_at=known, available_at=known, materialized_at=known, provenance='{}'))
            accession = f'{issuer}-26-000001'
            insert(db, 'sec_facts', dict(fact_key=f'fact-{i}', security_id=sid, qualified_symbol=symbol, ticker=f'SYN{i:02}', cik=issuer, taxonomy='us-gaap', concept='CashAndCashEquivalentsAtCarryingValue', value=1000000+i, unit='USD', currency='USD', period_end=datetime(2026,6,30).date(), form='10-Q', accession_number=accession, public_at=known, is_amendment=False, is_revision=False, source_endpoint=f'https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer}.json', retrieved_at=known))
            # Cover-page share count in a retained companyfacts document: ~10M shares x ~$100 = ~$1B.
            facts = json.dumps({'cik': int(issuer), 'facts': {'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
                {'accn': accession, 'end': '2026-07-25', 'filed': '2026-07-28', 'form': '10-Q', 'val': 10000000}]}}}}}, sort_keys=True, separators=(',', ':'))
            insert(db, 'sec_liquidity_raw_provenance', dict(evidence_key=f'companyfacts-{i}', operation_type='offline-synthetic', operation_contract_version='synthetic', concept_contract_hash='synthetic', lineage_id='synthetic', run_id='synthetic', plan_id='synthetic', security_id=sid, cik=issuer, endpoint_class='companyfacts', retrieved_at=known, response_sha256=hashlib.sha256(facts.encode()).hexdigest(), byte_count=len(facts), content_type='application/json', payload_json=facts, parser_version='synthetic'))
            payload = json.dumps({'cik': issuer, 'name': f'SYNTHETIC COMPANY {i:02} INC', 'sic': '3560', 'sicDescription': 'General Industrial Machinery & Equipment', 'entityType': 'operating'}, sort_keys=True)
            insert(db, 'sec_liquidity_raw_provenance', dict(evidence_key=f'submissions-{i}', operation_type='offline-synthetic', operation_contract_version='synthetic', concept_contract_hash='synthetic', lineage_id='synthetic', run_id='synthetic', plan_id='synthetic', security_id=sid, cik=issuer, endpoint_class='submissions', retrieved_at=known, response_sha256=hashlib.sha256(payload.encode()).hexdigest(), byte_count=len(payload), content_type='application/json', payload_json=payload, parser_version='synthetic'))
    return research, production


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--new-temporary-directory', required=True)
    args = parser.parse_args()
    paths = create_fixture(args.new_temporary_directory)
    print('SYNTHETIC ONLY — NOT THE ACTUAL OPERATOR ROSTER')
    print('\n'.join(str(p) for p in paths))


if __name__ == '__main__': main()
