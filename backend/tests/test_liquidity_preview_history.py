"""Realistic offline ingestion provenance and history; no producer calls."""
from datetime import date, datetime, timezone
import hashlib, json, subprocess, sys, uuid
import pytest
from pathlib import Path
import duckdb
from app import liquidity_evidence as le
from app.liquidity_canonical_contract import canonical_evidence_key
from app.liquidity_materialization import operation_identity
from app.liquidity_measurement import CONCEPT_FIELDS
from app.model_readiness import fingerprint
from app.sec_liquidity_plan import _encode_identifier, CONCEPTS
from app.sec_liquidity_contract import operation_identity as ingestion_identity
from datetime import timedelta

def sid(index): return str(uuid.uuid5(uuid.NAMESPACE_DNS,f"offline-company-{index}"))

BOUNDARIES = [datetime.fromisoformat(x) for x in ('2026-10-04T21:30:00+00:00','2026-10-05T00:30:00+00:00')]
PUBLIC=datetime(2026,7,1,tzinfo=timezone.utc)
MATERIALIZED=datetime(2026,10,4,23,tzinfo=timezone.utc)

def table(db,name,rows):
    columns=list(dict.fromkeys(k for r in rows for k in r))
    types={}
    for col in columns:
        value=next((r[col] for r in rows if r.get(col) is not None),None)
        types[col]='TIMESTAMPTZ' if isinstance(value,datetime) else 'DATE' if isinstance(value,date) else 'DOUBLE' if isinstance(value,(float,int)) else 'VARCHAR'
    db.execute('CREATE TABLE '+name+'('+','.join('"'+c+'" '+types[c] for c in columns)+')')
    db.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in columns)+')',[[r.get(c) for c in columns] for r in rows])

def fact(index,concept,value,end=date(2026,6,30),public=PUBLIC):
    symbol='NEU.US' if index==60 else f'C{index:02}.US'
    key=hashlib.sha256(f'{index}:{concept}:{end}:{public}'.encode()).hexdigest()
    return dict(fact_key=key,security_id=sid(index),qualified_symbol=symbol,taxonomy='us-gaap',taxonomy_version='2026',concept=concept,value=float(value),unit='USD',currency=None,scale=None,period_start=None,period_end=end,form='10-Q',accession_number=f'0000079783-{end.year%100:02d}-{end.month:06d}',public_at=public,retrieved_at=public,fiscal_year=end.year,fiscal_period='Q2',operation_type='sec_liquidity_ingestion',operation_contract_version='1.0.0',concept_contract_hash='a'*64,ingestion_run_id='00000000-0000-0000-0000-000000000001',ingestion_plan_id='b'*64)

def fixture(root):
    raw=[]; classifications=[]
    for i in range(69):
        symbol='NEU.US' if i==60 else f'C{i:02}.US'
        classifications.append(dict(security_id=sid(i),qualified_symbol=symbol,security_type='us_operating_company',public_at=PUBLIC,retrieved_at=PUBLIC,available_at=PUBLIC))
        if i<=60: raw.extend([fact(i,'AssetsCurrent',1_200_000_000+i),fact(i,'LiabilitiesCurrent',450_000_000+i)])
        if i>=4: raw.append(fact(i,'CashAndCashEquivalentsAtCarryingValue',250_000_000+i))
    assert len(raw)==187
    canonical=[]; revisions=[]; identity=operation_identity()
    for r in raw:
        field=CONCEPT_FIELDS[r['concept']]; key=canonical_evidence_key(r['fact_key'],field,BOUNDARIES[0])
        provenance={**identity,'materialization_run_id':'synthetic-run','decision_at':BOUNDARIES[0].isoformat(),'source_evidence_key':r['fact_key'],'original_scale':None}
        lineage={**identity,'source_evidence_key':r['fact_key'],'source_fact_key':r['fact_key']}
        canonical.append(dict(evidence_key=key,security_id=r['security_id'],qualified_symbol=r['qualified_symbol'],canonical_field=field,value=r['value'],unit='USD',currency='USD',period_start=None,period_end=r['period_end'],instant_date=r['period_end'],accession_or_source_identifier=r['accession_number'],public_at=PUBLIC,retrieved_at=PUBLIC,available_at=MATERIALIZED,materialized_at=MATERIALIZED,original_concept_or_field=r['concept'],source_fact_key=r['fact_key'],reliability_state='usable',provenance=json.dumps(provenance),lineage=json.dumps(lineage)))
        revisions.append(dict(**identity,evidence_key=key,security_id=r['security_id'],source_evidence_key=r['fact_key'],normalized_value=r['value'],original_concept=r['concept'],taxonomy=r['taxonomy'],original_unit='USD',original_currency=None,original_scale=None,canonical_unit='USD',canonical_currency='USD',period_end=r['period_end'],public_at=PUBLIC,retrieved_at=PUBLIC,applied_scale_factor=1))
    # Seventeen years of quarterly history, including stale and incompatible facts.
    for spec in le._SPECS:
        for year in range(2009,2027):
            for month in (3,6,9,12):
                end=date(year,month,30 if month in (6,9) else 31)
                if end>=date(2026,6,30): continue
                raw.append(fact(60,spec['exact_concept'],100_000_000+year*1000+month,end,datetime(year,month,28,tzinfo=timezone.utc)))
    issued=datetime(2026,10,4,20,tzinfo=timezone.utc)
    token=_encode_identifier(issued,dict(issued_at=issued.isoformat(),expires_at=(issued+timedelta(minutes=15)).isoformat(),decision_at=BOUNDARIES[0].isoformat(),fingerprints={'research':{'exists':True,'bytes':50000000,'sha256':'a'*64},'production':{'exists':True,'bytes':30000000,'sha256':'b'*64}},issuer_cohort=[dict(security_id=sid(i),qualified_symbol='NEU.US' if i==60 else f'C{i:02}.US',cik=f'{79783+i:010}') for i in range(69)],concepts=list(CONCEPTS),estimated_requests=138,request_budget=205,**ingestion_identity()))
    for row in raw:
        if row['concept'] in CONCEPTS: row['ingestion_plan_id']=token
        else:
            for key in ('operation_type','operation_contract_version','concept_contract_hash','ingestion_run_id','ingestion_plan_id'): row[key]=None
    raw.append(fact(60,'CustomCashFlow',12_000_000))
    raw.append({**fact(60,'InventoryNet',30_000_000),'unit':'shares'})
    raw.append(fact(60,'AssetsCurrent',9_999_999,date(2026,9,30),datetime(2026,10,6,tzinfo=timezone.utc)))
    research=root/'synthetic research.duckdb'; production=root/'synthetic production.duckdb'
    with duckdb.connect(str(research)) as db:
        table(db,'sec_facts',raw); table(db,'security_classification_evidence',classifications)
        table(db,'canonical_factor_evidence',canonical); table(db,'liquidity_canonical_materialization_revisions',revisions)
        table(db,'liquidity_canonical_materialization_runs',[dict(**identity,run_id='synthetic-run',status='completed',decision_at=BOUNDARIES[0])])
    with duckdb.connect(str(production)) as db: db.execute('CREATE TABLE marker(x INTEGER)')
    return research,production


@pytest.mark.parametrize("decision", BOUNDARIES)
def test_liquidity_preview_large_ingestion_plan_and_history(tmp_path,decision):
    research,production=fixture(tmp_path)
    before=[fingerprint(p) for p in (research,production)]
    kwargs=dict(research_db=research,production_db=production,decision_at=decision)
    _,companies,_=le._load(**kwargs)
    assert len(companies)==69 and companies[-1]["qualified_symbol"]=="NEU.US"
    original=companies[-1]
    plan=original["selected"]["current_assets"]["validation"]["lossless_normalization_provenance"]["controlled_ingestion"]["plan_id"]
    assert len(plan.encode())>10_000
    preview=le.company_preview(**kwargs,qualified_symbol="NEU.US")
    assert preview["compact_utf8_bytes"]==le.compact_utf8_size(preview)<=le.PREVIEW_MAXIMUM_BYTES
    company=preview["company"]
    assert company["observation_population"]["total_count"]==833
    assert company["observation_population"]["truncated"]
    assert all(x["returned_count"]<=3 for x in company["candidate_observations"].values())
    assert company["citations"]["returned_count"]<=10
    for field,selected in company["selected"].items():
        assert selected["value"]==original["selected"][field]["value"]
        assert selected["evidence_identity"]==original["selected"][field]["evidence_identity"]
        assert selected["accepted"]==original["selected"][field]["accepted"]
    observed=list(company["selected"].values())+[x for group in company["candidate_observations"].values() for x in group["items"]]
    references=[]
    sources={x["evidence_identity"]:x for x in original["observations"]}
    for observation in observed:
        controlled=observation["validation"]["lossless_normalization_provenance"]["controlled_ingestion"]
        if "plan_id_reference" in controlled:
            assert "plan_id" not in controlled
            reference=controlled["plan_id_reference"]
            source_plan=sources[observation["evidence_identity"]]["validation"]["lossless_normalization_provenance"]["controlled_ingestion"]["plan_id"]
            assert reference==dict(representation="sha256_utf8",sha256=hashlib.sha256(source_plan.encode()).hexdigest(),utf8_bytes=len(source_plan.encode()))
            references.append(reference)
    assert sum(x["utf8_bytes"]==len(plan.encode()) for x in references)>30
    assert plan not in json.dumps(preview,default=str)
    # Projection must not mutate the full validation result or its nested provenance.
    projected=le._preview_observation(original["selected"]["current_assets"])
    assert projected["validation"] is not original["selected"]["current_assets"]["validation"]
    assert original["selected"]["current_assets"]["validation"]["lossless_normalization_provenance"]["controlled_ingestion"]["plan_id"]==plan
    resolution=preview["database_immutability"]["canonical_liquidity_resolution"]
    expected=0 if decision==BOUNDARIES[0] else 187
    assert resolution["visible_selected_count"]==resolution["deduplicated_source_count"]==expected
    assert resolution["future_revision_count"]==187-expected and resolution["issue_counts"]=={}
    discovery=le.evidence_discovery(**kwargs)
    for concept,count in [("AssetsCurrent",61),("LiabilitiesCurrent",61),("CashAndCashEquivalentsAtCarryingValue",65)]:
        entry=next(x for x in discovery["concept_assessments"]["items"] if x["exact_concept"]==concept)
        assert entry["company_coverage_count"]==count
    contract=le.contract_assessment(**kwargs)
    assert le.compact_utf8_size(contract)<=le.CONTRACT_MAXIMUM_BYTES
    assert company["possible_constructions"]==original["possible_constructions"]
    assert preview["rankings"]==preview["recommendations"]==[] and preview["validation_credit"]==0
    command=[sys.executable,"-m","app.investment_research_cli","liquidity-company-preview","--research-db",str(research),"--production-db",str(production),"--decision-at",decision.isoformat(),"--qualified-symbol","NEU.US"]
    cli=subprocess.run(command,capture_output=True,text=True,cwd=Path(__file__).parents[1])
    assert cli.returncode==0,cli.stderr
    payload=json.loads(cli.stdout)
    assert payload["compact_utf8_bytes"]==preview["compact_utf8_bytes"]
    assert before==[fingerprint(p) for p in (research,production)]
