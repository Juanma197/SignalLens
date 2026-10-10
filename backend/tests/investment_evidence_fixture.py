"""Synthetic research database for stored-evidence materialization tests.

Covers each evidence path: concordant and conflicting classifications, filing
regimes, revised and not-yet-public facts, incompatible units, missing and
withheld prices, corporate actions and verified-no-action checkpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from app.sec_ingestion import SCHEMA as SEC_SCHEMA

DECISION = datetime(2026, 10, 1, tzinfo=timezone.utc)
CONCEPTS = (("Revenues", "USD"), ("NetIncomeLoss", "USD"), ("Assets", "USD"),
            ("NetCashProvidedByUsedInOperatingActivities", "USD"),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
            ("EarningsPerShareBasic", "USD/shares"), ("NotAnAliasedConcept", "USD"))


def create(path: Path, companies: int = 12, years: int = 4) -> None:
    utc = timezone.utc
    with duckdb.connect(str(path)) as db:
        db.execute(SEC_SCHEMA)
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR,retrieved_at TIMESTAMP,status VARCHAR)")
        db.execute("""CREATE TABLE security_listings(retrieval_id VARCHAR,security_id VARCHAR,ticker VARCHAR,
            qualified_symbol VARCHAR,primary_exchange VARCHAR,currency VARCHAR,instrument_type VARCHAR,
            active BOOLEAN,cik VARCHAR,retrieved_at TIMESTAMP)""")
        db.execute("""CREATE TABLE sec_entity_metadata(cik VARCHAR,security_type VARCHAR,source_identifier VARCHAR,
            public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE reviewed_security_classifications(security_id VARCHAR,security_type VARCHAR,
            review_id VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE global_price_observations(qualified_symbol VARCHAR,trading_date DATE,currency VARCHAR,
            adjusted_close DOUBLE,volume BIGINT,status VARCHAR,source VARCHAR,retrieved_at TIMESTAMP)""")
        db.execute("""CREATE TABLE global_corporate_actions(qualified_symbol VARCHAR,ex_date DATE,action_type VARCHAR,
            value DOUBLE,source VARCHAR,retrieved_at TIMESTAMP)""")
        db.execute("""CREATE TABLE eodhd_ingestion_checkpoints(stage VARCHAR,qualified_symbol VARCHAR,status VARCHAR,
            updated_at TIMESTAMP)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','2026-01-01','completed')")
        db.execute("INSERT INTO security_listings VALUES ('r','intl','ZZZ','ZZZ.LSE','LSE','GBP','common_stock',true,NULL,'2026-01-01')")
        stored = datetime(2026, 9, 1, tzinfo=utc)
        for n in range(companies):
            sid, ticker = f"sec-{n:03d}", f"C{n:03d}"; symbol = f"{ticker}.US"; cik = f"{n + 1:010d}"
            kind = "adr" if n % 8 == 7 else "common_stock"
            db.execute("INSERT INTO security_listings VALUES ('r',?,?,?,'US','USD',?,true,?,'2026-01-01')",
                       [sid, ticker, symbol, kind, cik])
            if n % 6 != 5:
                db.execute("INSERT INTO sec_issuers VALUES (?,?,?,?,?,'fixture',?)", [sid, symbol, ticker, cik, ticker, stored])
            if n % 6 == 4:  # a second, conflicting issuer and regime
                db.execute("INSERT INTO sec_issuers VALUES (?,?,?,?,?,'fixture',?)", [sid, symbol, ticker, f"{n + 501:010d}", ticker, stored])
                db.execute("INSERT INTO sec_filings VALUES (?,'a-x','20-F',NULL,?,false,'fixture',?)", [f"{n + 501:010d}", stored, stored])
            if n % 5 == 0:
                db.execute("INSERT INTO sec_entity_metadata VALUES (?,'us_operating_company','meta',?,?)", [cik, stored, stored])
            if n % 7 == 3:
                db.execute("INSERT INTO reviewed_security_classifications VALUES (?,'reit','review',?,?)", [sid, stored, stored])
            db.execute("INSERT INTO sec_filings VALUES (?,?,?,NULL,?,false,'fixture',?)",
                       [cik, f"a-{n}", "10-K" if n % 3 else "10-Q", stored, stored])
            db.execute("INSERT INTO sec_filings VALUES (?,?,'10-K',NULL,?,false,'fixture',?)",  # not public yet
                       [cik, f"late-{n}", DECISION + timedelta(days=3), DECISION + timedelta(days=3)])
            facts = []
            for year in range(2022, 2022 + years):
                for c, (concept, unit) in enumerate(CONCEPTS):
                    if (n + c) % 9 == 0: continue
                    period = (date(year, 1, 1), date(year, 12, 31))
                    public = datetime(year + 1, 2, 15, tzinfo=utc)
                    value = 1000.0 * (n + 1) + c * 10 + year
                    use_unit = "EUR" if (n + c + year) % 11 == 0 and unit == "USD" else unit
                    facts.append((f"{sid}-{concept}-{year}", concept, value, use_unit, period, public, year))
                    if (n + year) % 3 == 0:  # a later revision of the same period
                        facts.append((f"{sid}-{concept}-{year}-r", concept, value + 1, use_unit, period, public + timedelta(days=90), year))
                    if year == 2022 + years - 1 and c == 0:  # filed after the decision
                        facts.append((f"{sid}-{concept}-{year}-future", concept, value + 2, use_unit, period, DECISION + timedelta(days=1), year))
            for key, concept, value, unit, (start, end), public, year in facts:
                instant = concept == "Assets"
                db.execute("""INSERT INTO sec_facts VALUES (?,?,?,?,?,'us-gaap',?,?,?,?,?,?,?,'FY',NULL,'10-K',?,NULL,?,false,false,'fixture',?)""",
                           [key, sid, symbol, ticker, cik, concept, value, unit, "USD" if unit != "EUR" else "EUR",
                            None if instant else start, end, year, f"acc-{key}", public, public])
            if n % 4 != 3:
                for d in range(30):
                    day = date(2026, 8, 15) + timedelta(days=d)
                    status = "missing" if d == 25 else "available"
                    close = None if (n % 8 == 2 and d == 29) else 10.0 + n + d / 10
                    db.execute("INSERT INTO global_price_observations VALUES (?,?,'USD',?,1000,?,'eodhd',?)",
                               [symbol, day, close, status, datetime(2026, 9, 20)])
                db.execute("INSERT INTO global_price_observations VALUES (?,?,'USD',99.0,1000,'available','eodhd',?)",
                           [symbol, date(2026, 10, 5), datetime(2026, 10, 6)])
            if n % 3 == 0:
                db.execute("INSERT INTO global_corporate_actions VALUES (?,?,'dividend',0.5,'eodhd',?)", [symbol, date(2026, 3, 1), datetime(2026, 9, 20)])
                db.execute("INSERT INTO global_corporate_actions VALUES (?,?,'dividend',0.5,'eodhd',?)", [symbol, date(2026, 10, 3), datetime(2026, 9, 20)])
            elif n % 3 == 1:
                db.execute("INSERT INTO eodhd_ingestion_checkpoints VALUES ('corporate_actions',?,'completed',?)", [symbol, datetime(2026, 9, 20)])
