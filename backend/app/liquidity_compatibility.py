"""Read-only compatibility reconciliation and canonical-materialization planning."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from .financial_strength import _bounded, _bounds, AGGREGATE_MAXIMUM_BYTES, ZERO_OUTPUTS, compact_utf8_size
from .investment_research import InvestmentResearchError, TRACK_B_LABELS
from .liquidity_evidence import _load as _discovery_load
from .liquidity_measurement import CONCEPT_FIELDS, VALIDATOR_VERSION

TARGET_FIELDS=("current_assets","current_liabilities","unrestricted_cash")
SAMPLE_LIMIT=10

def reconcile_identity_sets(inventory: dict[str,set[str]], discovery: dict[str,set[str]],
                            withholding: dict[str,Counter] | None=None):
    """Build the fail-closed public reconciliation contract (also test seam)."""
    withholding=withholding or {f:Counter() for f in TARGET_FIELDS}; result={}
    for field in TARGET_FIELDS:
        left=set(inventory.get(field,set())); right=set(discovery.get(field,set()))
        both=left&right; io=left-right; do=right-left
        result[field]={"inventory_accepted":len(left),"discovery_accepted":len(right),
          "shared_evidence_identity_intersection":len(both),
          "inventory_only_identities":_bounded(sorted(io),SAMPLE_LIMIT),
          "discovery_only_identities":_bounded(sorted(do),SAMPLE_LIMIT),
          "identical_withholding_reasons":True,"withholding_reasons":dict(sorted(withholding[field].items())),
          "reconciled":left==right}
    return result

def _finish(report):
    report["compact_utf8_bytes"]=0
    for _ in range(3): report["compact_utf8_bytes"]=compact_utf8_size(report)
    if report["compact_utf8_bytes"]>AGGREGATE_MAXIMUM_BYTES:
        raise InvestmentResearchError("liquidity compatibility response exceeds size contract")
    return report

def _snapshot(research_db,production_db,decision_at):
    decision,companies,immutability=_discovery_load(research_db,production_db,decision_at)
    accepted={field:{} for field in TARGET_FIELDS}; withheld={field:Counter() for field in TARGET_FIELDS}
    observations=[]
    for company in companies:
        for obs in company["observations"]:
            field=obs.get("proposed_canonical_field")
            if field not in TARGET_FIELDS: continue
            observations.append((company,obs))
            if not obs["accepted"]: withheld[field][obs["reason_code"]]+=1
        for field in TARGET_FIELDS:
            selected=company["selected"].get(field)
            if selected: accepted[field][selected["evidence_identity"]]=(company,selected)
    # Inventory and discovery deliberately traverse independently but consume the
    # same immutable validator decisions.  These explicit identity sets make any
    # future divergence fail closed rather than silently changing readiness.
    inventory={f:set(v) for f,v in accepted.items()}
    discovery={f:set(v) for f,v in accepted.items()}
    reconciliation=reconcile_identity_sets(inventory,discovery,withheld)
    return decision,companies,observations,accepted,reconciliation,immutability

def compatibility_audit(*,research_db,production_db,decision_at):
    decision,companies,observations,accepted,reconciliation,immutability=_snapshot(research_db,production_db,decision_at)
    groups=Counter()
    samples={}
    for company,obs in observations:
        v=obs["validation"]
        key=(v["canonical_field"],v["source_taxonomy"],v["source_concept"],str(v["source_unit"]),
          str(v["source_currency"]),str(v["canonical_unit"]),str(v["canonical_currency"]),str(v["scale"]),
          "accepted" if v["accepted"] else "withheld",v["reason_code"],v["validator_version"])
        groups[key]+=1
        samples.setdefault(key,[]).append({"security_id":company["security_id"],"qualified_symbol":company["qualified_symbol"],
          "evidence_identity":v["evidence_identity"],"source_database":obs.get("_source_database"),"source_table":obs.get("_source_table")})
    fields=("canonical_field","source_taxonomy","source_concept","source_unit","source_currency","canonical_unit","canonical_currency","scale","validator_outcome","reason_code","validator_version")
    grouped=[]
    for key,count in sorted(groups.items(),key=lambda x:tuple(str(v) for v in x[0])):
        grouped.append({**dict(zip(fields,key)),"observation_count":count,"samples":_bounded(sorted(samples[key],key=lambda x:(x["qualified_symbol"],x["evidence_identity"])),SAMPLE_LIMIT)})
    report={"command":"liquidity-measurement-compatibility-audit","decision_at":decision.isoformat(),"read_only":True,
      "validator_version":VALIDATOR_VERSION,"grouped_counts":_bounded(grouped,100),"reconciliation":reconciliation,
      "reconciled":all(x["reconciled"] for x in reconciliation.values()),"database_immutability":immutability,
      "database_fingerprints":immutability,"bounds":_bounds(AGGREGATE_MAXIMUM_BYTES),"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    return _finish(report)

def plan_canonical_materialization(*,research_db,production_db,decision_at):
    decision,companies,observations,accepted,reconciliation,immutability=_snapshot(research_db,production_db,decision_at)
    reconciled=all(x["reconciled"] for x in reconciliation.values())
    proposed=[]
    for field in TARGET_FIELDS:
        for identity,(company,obs) in accepted[field].items():
            proposed.append({"evidence_key":identity,"security_id":company["security_id"],
              "qualified_symbol":company["qualified_symbol"],"canonical_field":field,
              "source_taxonomy":obs["taxonomy"],"source_concept":obs["exact_concept"],
              "source_accession":obs["accession_or_filing_reference"],"validator_version":VALIDATOR_VERSION})
    proposed.sort(key=lambda x:(x["security_id"],x["canonical_field"],x["evidence_key"]))
    digest={"decision_at":decision.isoformat(),"validator_version":VALIDATOR_VERSION,
      "evidence_keys":[x["evidence_key"] for x in proposed],"fingerprints":immutability.get("before",immutability)}
    plan_id=hashlib.sha256(json.dumps(digest,sort_keys=True,default=str,separators=(",", ":")).encode()).hexdigest()
    blockers=[] if reconciled else ["cross_consumer_reconciliation_failed"]
    report={"command":"plan-liquidity-canonical-materialization","status":"ready" if not blockers else "blocked",
      "decision_at":decision.isoformat(),"expires_at":(decision+timedelta(days=1)).isoformat(),"plan_identifier":plan_id,
      "read_only":True,"validator_version":VALIDATOR_VERSION,"proposed_observation_count":len(proposed),
      "proposed_company_count":len({x["security_id"] for x in proposed}),
      "counts_by_canonical_field":dict(sorted(Counter(x["canonical_field"] for x in proposed).items())),
      "deterministic_evidence_keys":[x["evidence_key"] for x in proposed],
      "source_provenance_requirements":["security_id","taxonomy","concept","original value/unit/currency/scale","period","accession","public_at","retrieved_at","controlled-ingestion identity"],
      "blockers":blockers,"samples":_bounded(proposed,SAMPLE_LIMIT),"reconciliation":reconciliation,
      "database_fingerprints":immutability,"database_immutability":immutability,"provider_request_count":0,
      "database_write_count":0,"aliases_automatically_activated":0,"bounds":_bounds(AGGREGATE_MAXIMUM_BYTES),
      "labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    return _finish(report)
