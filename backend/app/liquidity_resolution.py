"""Fail-closed resolution of controlled canonical liquidity revisions.

The canonical table is append-only: a materialized observation intentionally
coexists with its SEC source fact.  Consumers must therefore use this module,
rather than treating the two rows as unrelated observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from typing import Any

from .liquidity_measurement import CONCEPT_FIELDS, VALIDATOR_VERSION, evidence_identity

OPERATION_TYPE = "liquidity_canonical_materialization"
OPERATION_CONTRACT_VERSION = "1.0.0"
TARGET_FIELDS = ("current_assets", "current_liabilities", "unrestricted_cash")
SAMPLE_LIMIT = 10


class CanonicalLiquidityResolutionError(RuntimeError):
    """An exact controlled revision could not be resolved safely."""

    reason_code = "LIQUIDITY_CANONICAL_RESOLUTION_FAILED"

    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        super().__init__(self.reason_code)


def _json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo is not None and result.utcoffset() is not None else None


def _same_number(left: Any, right: Any) -> bool:
    try:
        return math.isfinite(float(left)) and math.isfinite(float(right)) and float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _key(source_key: str, field: str, decision_at: Any) -> str:
    payload = json.dumps([OPERATION_TYPE, source_key, field, str(decision_at)],
                         sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def resolve_canonical_liquidity(*, raw_rows: list[dict[str, Any]],
                                canonical_rows: list[dict[str, Any]],
                                revision_rows: list[dict[str, Any]],
                                run_rows: list[dict[str, Any]], decision_at: datetime,
                                contract_hash: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return effective liquidity rows and a bounded provenance diagnostic.

    Non-liquidity and legacy canonical rows are passed through.  Exact controlled
    revisions replace their one source row only after ``materialized_at`` is
    visible.  The source itself remains represented in the diagnostic.
    """
    raw_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        raw_by_key[evidence_identity(row)].append(row)
    revisions=defaultdict(list)
    for row in revision_rows:
        revisions[str(row.get("evidence_key"))].append(row)
    runs={str(row.get("run_id")):row for row in run_rows if row.get("status")=="completed"}
    issues: Counter[str] = Counter(); samples: list[dict[str, str]]=[]
    candidates=defaultdict(list); controlled_keys=set()

    def reject(reason: str, row: dict[str, Any]):
        issues[reason]+=1
        if len(samples)<SAMPLE_LIMIT:
            samples.append({"reason":reason,"evidence_key":str(row.get("evidence_key",""))[:64],
                            "source_evidence_key":str(row.get("source_fact_key",""))[:64]})

    for canonical in canonical_rows:
        provenance=_json(canonical.get("provenance")); lineage=_json(canonical.get("lineage"))
        if not provenance or provenance.get("operation_type") != OPERATION_TYPE:
            continue
        controlled_keys.add(str(canonical.get("evidence_key")))
        evidence_key=str(canonical.get("evidence_key") or "")
        linked=revisions.get(evidence_key,[])
        if len(linked)!=1:
            reject("duplicate_or_missing_revision",canonical); continue
        revision=linked[0]; source_key=str(canonical.get("source_fact_key") or "")
        source_matches=raw_by_key.get(source_key,[])
        if len(source_matches)!=1:
            reject("ambiguous_or_unmatched_source",canonical); continue
        source=source_matches[0]; field=canonical.get("canonical_field")
        run=runs.get(str(provenance.get("materialization_run_id")))
        expected_identity=(OPERATION_TYPE,OPERATION_CONTRACT_VERSION,contract_hash,VALIDATOR_VERSION)
        identities=(provenance.get("operation_type"),provenance.get("operation_contract_version"),
                    provenance.get("operation_contract_hash"),provenance.get("validator_version"))
        lineage_identity=(lineage.get("operation_type"),OPERATION_CONTRACT_VERSION,
                          lineage.get("operation_contract_hash"),lineage.get("validator_version")) if lineage else None
        revision_identity=(revision.get("operation_type"),revision.get("operation_contract_version"),
                           revision.get("operation_contract_hash"),revision.get("validator_version"))
        run_identity=(run.get("operation_type"),run.get("operation_contract_version"),
                      run.get("operation_contract_hash"),run.get("validator_version")) if run else None
        reason=None
        if not lineage: reason="malformed_lineage"
        elif identities!=expected_identity or lineage_identity!=expected_identity or revision_identity!=expected_identity or run_identity!=expected_identity:
            reason="incompatible_operation_identity"
        elif field not in TARGET_FIELDS or CONCEPT_FIELDS.get(str(source.get("concept")))!=field:
            reason="canonical_field_mismatch"
        elif any(str(x)!=source_key for x in (provenance.get("source_evidence_key"),lineage.get("source_evidence_key"),lineage.get("source_fact_key"),revision.get("source_evidence_key"))):
            reason="source_lineage_mismatch"
        elif str(canonical.get("security_id"))!=str(source.get("security_id")) or str(revision.get("security_id"))!=str(source.get("security_id")):
            reason="security_id_mismatch"
        elif _aware(provenance.get("decision_at")) != _aware(run.get("decision_at")):
            reason="materialization_decision_mismatch"
        elif evidence_key != _key(source_key,str(field),_aware(run.get("decision_at")).isoformat()):
            reason="canonical_evidence_key_mismatch"
        elif not _same_number(canonical.get("value"),revision.get("normalized_value")) or not _same_number(canonical.get("value"),source.get("value")):
            reason="conflicting_canonical_value"
        elif str(revision.get("original_concept"))!=str(source.get("concept")) or str(canonical.get("original_concept_or_field"))!=str(source.get("concept")) or str(revision.get("taxonomy"))!=str(source.get("taxonomy")):
            reason="accounting_meaning_mismatch"
        elif str(revision.get("original_unit"))!=str(source.get("unit")) or str(revision.get("original_currency"))!=str(source.get("currency")):
            reason="source_unit_or_currency_mismatch"
        elif revision.get("original_scale") not in (None,0,1) or provenance.get("original_scale") not in (None,0,1):
            reason="unsupported_scale"
        elif any(str(x or "")!="USD" for x in (canonical.get("unit"),canonical.get("currency"),revision.get("canonical_unit"),revision.get("canonical_currency"))):
            reason="unit_or_currency_mismatch"
        elif str(canonical.get("period_end"))!=str(source.get("period_end")) or str(revision.get("period_end"))!=str(source.get("period_end")):
            reason="period_mismatch"
        elif str(canonical.get("accession_or_source_identifier"))!=str(source.get("accession_number")):
            reason="source_identity_mismatch"
        elif any(_iso(canonical.get(k))!=_iso(source.get(k)) for k in ("public_at","retrieved_at")):
            reason="source_timestamp_mismatch"
        elif any(_iso(revision.get(k))!=_iso(source.get(k)) for k in ("public_at","retrieved_at")):
            reason="revision_timestamp_mismatch"
        elif not _same_number(revision.get("applied_scale_factor"),1):
            reason="unsupported_scale"
        materialized=_aware(canonical.get("materialized_at")); available=_aware(canonical.get("available_at"))
        if not reason and (not materialized or not available or available != max(materialized,_aware(source.get("public_at")),_aware(source.get("retrieved_at")))):
            reason="availability_timestamp_mismatch"
        if reason:
            reject(reason,canonical); continue
        # Preserve the raw timestamps for accounting validation; visibility of
        # the revision itself has already been enforced above.
        effective={**source,"value":canonical["value"],"evidence_key":evidence_key,
                   "fact_key":evidence_key,"canonical_field":field,
                   "original_concept_or_field":source.get("concept"),
                   "accession_or_source_identifier":source.get("accession_number"),
                   "reliability_state":"usable","currency":"USD","unit":"USD",
                   "_canonical_revision":True,"_canonical_available_at":available,
                   "_source_evidence_key":source_key,"_canonical_row":canonical}
        candidates[(source_key,field)].append((materialized,available,evidence_key,effective))

    if issues:
        raise CanonicalLiquidityResolutionError({"issue_counts":dict(sorted(issues.items())),
          "samples":samples,"sample_limit":SAMPLE_LIMIT,"samples_truncated":sum(issues.values())>len(samples)})
    selected={}; visible=0; future=0
    for identity, group in candidates.items():
        visible_group=[x for x in group if x[1] <= decision_at]
        future+=len(group)-len(visible_group)
        if visible_group:
            winner=max(visible_group,key=lambda x:(x[0],x[1],x[2]))
            values={float(x[3]["value"]) for x in visible_group}
            if len(values)>1:
                raise CanonicalLiquidityResolutionError({"issue_counts":{"conflicting_visible_revisions":1},
                  "samples":[{"source_evidence_key":identity[0][:64],"canonical_field":identity[1]}],
                  "sample_limit":SAMPLE_LIMIT,"samples_truncated":False})
            selected[identity[0]]=winner[3]; visible+=1
    effective=[]
    for row in raw_rows:
        key=evidence_identity(row)
        effective.append(selected.get(key,row))
    effective.extend(row for row in canonical_rows if str(row.get("evidence_key")) not in controlled_keys)
    diagnostic={"resolver_version":"canonical-liquidity-resolver-1.0.0",
      "controlled_revision_count":len(controlled_keys),"visible_selected_count":visible,
      "future_revision_count":future,"deduplicated_source_count":visible,
      "retained_source_count":len(raw_rows),"issue_counts":{},"samples":[],"sample_limit":SAMPLE_LIMIT}
    return effective,diagnostic
