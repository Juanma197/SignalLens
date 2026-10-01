"""Storage-free, offline international point-in-time source assessment."""
from __future__ import annotations

import duckdb

from .active_catalogue import select_active_catalogue

ASSESSMENT = (
 {"region":"LSE","representative":"catalogue-first eligible LSE security","source":"UK Companies House filing history/document API","access":"official API; registration may be required","history":"filed accounts and document images","public_timestamp":"filing received/accepted date; verify semantics before ingestion","revisions":"separate filing history entries","units_currencies":"document/XBRL dependent (commonly GBP)","automation":"feasible with XBRL/document parsing","limits":"published API rate limits apply","licensing":"Open Government Licence terms require review","backtest_suitability":"candidate; not live-confirmed"},
 {"region":"TO","representative":"catalogue-first eligible TO security","source":"SEDAR+ public filings","access":"official public web portal; no validated bulk API","history":"historical issuer filings","public_timestamp":"filing timestamp must be captured and validated","revisions":"amended filings identifiable","units_currencies":"filing dependent (commonly CAD/USD)","automation":"uncertain; portal terms and access require review","limits":"not assessed offline","licensing":"terms of use and redistribution require review","backtest_suitability":"assessment required; not live-confirmed"},
 {"region":"XETRA","representative":"catalogue-first eligible XETRA security","source":"Unternehmensregister (German Company Register)","access":"official register web service","history":"annual/interim disclosures vary by issuer","public_timestamp":"register publication time/date must be validated","revisions":"publication versions may be distinguishable","units_currencies":"filing dependent (commonly EUR)","automation":"uncertain; formats and access controls vary","limits":"not assessed offline","licensing":"register terms/fees require review","backtest_suitability":"candidate only after timestamp/version proof; not live-confirmed"},
 {"region":"PA","representative":"catalogue-first eligible PA security","source":"AMF BDIF / issuer regulated-information filings","access":"official AMF search/download services","history":"regulated filings and issuer documents","public_timestamp":"dissemination/publication timestamp must be validated","revisions":"corrective filings may be separate","units_currencies":"document dependent (commonly EUR)","automation":"uncertain; structured coverage varies","limits":"not assessed offline","licensing":"AMF terms and issuer document rights require review","backtest_suitability":"candidate only after timestamp/version proof; not live-confirmed"},
)


def capability_report(connection: duckdb.DuckDBPyConnection | None = None) -> dict:
    representatives = {}
    if connection is not None:
        catalogue = select_active_catalogue(connection)
        if catalogue is not None:
            eligible = catalogue.listings.loc[catalogue.listings["eligible"].astype(bool)]
            for region in ("LSE", "TO", "XETRA", "PA"):
                rows = eligible.loc[eligible["region"].eq(region)]
                representatives[region] = None if rows.empty else str(rows.iloc[0]["qualified_symbol"])
    regions = [{**row, "representative": representatives.get(row["region"], row["representative"])}
               for row in ASSESSMENT]
    return {"mode":"offline_documentary_assessment","live_confirmation":False,
        "storage_writes":0,"regions":regions,
        "yahoo_finance":{"classification":"latest-only/unsuitable","authoritative":False,
            "point_in_time_backtesting":False},
        "constraint":"International securities remain on the unchanged global price-only baseline until comparable point-in-time evidence exists."}
