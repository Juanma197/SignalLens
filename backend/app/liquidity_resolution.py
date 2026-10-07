"""Fail-closed resolution of controlled canonical liquidity revisions.

The canonical table is append-only: a materialized observation intentionally
coexists with its SEC source fact. Consumers must therefore use this module,
rather than treating the two rows as unrelated observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .liquidity_measurement import (
    CONCEPT_FIELDS,
    VALIDATOR_VERSION,
    evidence_identity,
)
from .liquidity_canonical_contract import (
    OPERATION_TYPE,
    OPERATION_CONTRACT_VERSION,
    canonical_available_at,
    canonical_evidence_key,
    date_value,
    identity_scale,
    parse_object,
    redundant_currency,
    same_number,
    timestamp_text,
    utc_datetime,
)

TARGET_FIELDS = ("current_assets", "current_liabilities", "unrestricted_cash")
SAMPLE_LIMIT = 10


class CanonicalLiquidityResolutionError(RuntimeError):
    """An exact controlled revision could not be resolved safely."""

    reason_code = "LIQUIDITY_CANONICAL_RESOLUTION_FAILED"

    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        super().__init__(self.reason_code)


def resolve_canonical_liquidity(
    *,
    raw_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
    revision_rows: list[dict[str, Any]],
    run_rows: list[dict[str, Any]],
    decision_at: datetime,
    contract_hash: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return effective liquidity rows and a bounded provenance diagnostic.

    Non-liquidity and legacy canonical rows are passed through. Exact controlled
    revisions replace their one source row only after materialized_at is
    visible. The source itself remains represented in the diagnostic.
    """
    raw_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        raw_by_key[evidence_identity(row)].append(row)

    revisions = defaultdict(list)
    for row in revision_rows:
        revisions[str(row.get("evidence_key"))].append(row)

    runs = {
        str(row.get("run_id")): row
        for row in run_rows
        if row.get("status") == "completed"
    }

    issues: Counter[str] = Counter()
    samples: list[dict[str, str]] = []
    candidates = defaultdict(list)
    controlled_keys = set()

    def reject(reason: str, row: dict[str, Any]):
        issues[reason] += 1
        if len(samples) < SAMPLE_LIMIT:
            samples.append({
                "reason": reason,
                "evidence_key": str(row.get("evidence_key", ""))[:64],
                "source_evidence_key": str(row.get("source_fact_key", ""))[:64],
            })

    for canonical in canonical_rows:
        provenance = parse_object(canonical.get("provenance"))
        lineage = parse_object(canonical.get("lineage"))

        if not provenance or provenance.get("operation_type") != OPERATION_TYPE:
            continue

        controlled_keys.add(str(canonical.get("evidence_key")))
        evidence_key = str(canonical.get("evidence_key") or "")
        linked = revisions.get(evidence_key, [])

        if len(linked) != 1:
            reject("duplicate_or_missing_revision", canonical)
            continue

        revision = linked[0]
        source_key = str(canonical.get("source_fact_key") or "")
        source_matches = raw_by_key.get(source_key, [])

        if len(source_matches) != 1:
            reject("ambiguous_or_unmatched_source", canonical)
            continue

        source = source_matches[0]
        field = canonical.get("canonical_field")
        run = runs.get(str(provenance.get("materialization_run_id")))

        expected_identity = (
            OPERATION_TYPE,
            OPERATION_CONTRACT_VERSION,
            contract_hash,
            VALIDATOR_VERSION,
        )
        identities = (
            provenance.get("operation_type"),
            provenance.get("operation_contract_version"),
            provenance.get("operation_contract_hash"),
            provenance.get("validator_version"),
        )
        lineage_identity = (
            (
                lineage.get("operation_type"),
                OPERATION_CONTRACT_VERSION,
                lineage.get("operation_contract_hash"),
                lineage.get("validator_version"),
            )
            if lineage
            else None
        )
        revision_identity = (
            revision.get("operation_type"),
            revision.get("operation_contract_version"),
            revision.get("operation_contract_hash"),
            revision.get("validator_version"),
        )
        run_identity = (
            (
                run.get("operation_type"),
                run.get("operation_contract_version"),
                run.get("operation_contract_hash"),
                run.get("validator_version"),
            )
            if run
            else None
        )

        reason = None

        if not lineage:
            reason = "malformed_lineage"
        elif (
            identities != expected_identity
            or lineage_identity != expected_identity
            or revision_identity != expected_identity
            or run_identity != expected_identity
        ):
            reason = "incompatible_operation_identity"
        elif (
            field not in TARGET_FIELDS
            or CONCEPT_FIELDS.get(str(source.get("concept"))) != field
        ):
            reason = "canonical_field_mismatch"
        elif any(
            str(value) != source_key
            for value in (
                provenance.get("source_evidence_key"),
                lineage.get("source_evidence_key"),
                lineage.get("source_fact_key"),
                revision.get("source_evidence_key"),
            )
        ):
            reason = "source_lineage_mismatch"
        elif (
            str(canonical.get("security_id")) != str(source.get("security_id"))
            or str(revision.get("security_id")) != str(source.get("security_id"))
        ):
            reason = "security_id_mismatch"
        elif timestamp_text(provenance.get("decision_at")) != timestamp_text(
            run.get("decision_at")
        ):
            reason = "materialization_decision_mismatch"
        elif evidence_key != canonical_evidence_key(
            source_key, str(field), run.get("decision_at")
        ):
            reason = "canonical_evidence_key_mismatch"
        elif (
            not same_number(
                canonical.get("value"), revision.get("normalized_value")
            )
            or not same_number(canonical.get("value"), source.get("value"))
        ):
            reason = "conflicting_canonical_value"
        elif (
            str(revision.get("original_concept")) != str(source.get("concept"))
            or str(canonical.get("original_concept_or_field"))
            != str(source.get("concept"))
            or str(revision.get("taxonomy")) != str(source.get("taxonomy"))
        ):
            reason = "accounting_meaning_mismatch"
        elif (
            str(revision.get("original_unit")) != str(source.get("unit"))
            or redundant_currency(
                revision.get("original_currency"), revision.get("original_unit")
            )
            != redundant_currency(source.get("currency"), source.get("unit"))
        ):
            reason = "source_unit_or_currency_mismatch"
        elif (
            identity_scale(revision.get("original_scale")) is None
            or identity_scale(provenance.get("original_scale")) is None
        ):
            reason = "unsupported_scale"
        elif any(
            str(value or "") != "USD"
            for value in (
                canonical.get("unit"),
                canonical.get("currency"),
                revision.get("canonical_unit"),
                revision.get("canonical_currency"),
            )
        ):
            reason = "unit_or_currency_mismatch"
        elif (
            date_value(canonical.get("period_end"))
            != date_value(source.get("period_end"))
            or date_value(revision.get("period_end"))
            != date_value(source.get("period_end"))
        ):
            reason = "period_mismatch"
        elif str(canonical.get("accession_or_source_identifier")) != str(
            source.get("accession_number")
        ):
            reason = "source_identity_mismatch"
        elif any(
            timestamp_text(canonical.get(key)) != timestamp_text(source.get(key))
            for key in ("public_at", "retrieved_at")
        ):
            reason = "source_timestamp_mismatch"
        elif any(
            timestamp_text(revision.get(key)) != timestamp_text(source.get(key))
            for key in ("public_at", "retrieved_at")
        ):
            reason = "revision_timestamp_mismatch"
        elif not same_number(revision.get("applied_scale_factor"), 1):
            reason = "unsupported_scale"

        materialized = utc_datetime(canonical.get("materialized_at"))
        available = utc_datetime(canonical.get("available_at"))
        expected_available = None

        if materialized:
            try:
                expected_available = canonical_available_at(
                    public_at=source.get("public_at"),
                    retrieved_at=source.get("retrieved_at"),
                    materialized_at=materialized,
                )
            except ValueError:
                pass

        if not reason and (not available or available != expected_available):
            reason = "availability_timestamp_mismatch"

        if reason:
            reject(reason, canonical)
            continue

        # Preserve the raw timestamps for accounting validation.
        # Canonical visibility is enforced when selecting candidates below.
        effective = {
            **source,
            "value": canonical["value"],
            "evidence_key": evidence_key,
            "fact_key": evidence_key,
            "canonical_field": field,
            "original_concept_or_field": source.get("concept"),
            "accession_or_source_identifier": source.get("accession_number"),
            "reliability_state": "usable",
            "currency": "USD",
            "unit": "USD",
            "_canonical_revision": True,
            "_canonical_available_at": available,
            "_source_evidence_key": source_key,
            "_canonical_row": canonical,
        }
        candidates[(source_key, field)].append(
            (materialized, available, evidence_key, effective)
        )

    if issues:
        raise CanonicalLiquidityResolutionError({
            "issue_counts": dict(sorted(issues.items())),
            "samples": samples,
            "sample_limit": SAMPLE_LIMIT,
            "samples_truncated": sum(issues.values()) > len(samples),
        })

    selected = {}
    visible = 0
    future = 0

    for identity, group in candidates.items():
        visible_group = [item for item in group if item[1] <= decision_at]
        future += len(group) - len(visible_group)

        if visible_group:
            winner = max(
                visible_group, key=lambda item: (item[0], item[1], item[2])
            )
            values = {float(item[3]["value"]) for item in visible_group}

            if len(values) > 1:
                raise CanonicalLiquidityResolutionError({
                    "issue_counts": {"conflicting_visible_revisions": 1},
                    "samples": [{
                        "source_evidence_key": identity[0][:64],
                        "canonical_field": identity[1],
                    }],
                    "sample_limit": SAMPLE_LIMIT,
                    "samples_truncated": False,
                })

            selected[identity[0]] = winner[3]
            visible += 1

    effective = []
    for row in raw_rows:
        key = evidence_identity(row)
        effective.append(selected.get(key, row))

    effective.extend(
        row
        for row in canonical_rows
        if str(row.get("evidence_key")) not in controlled_keys
    )

    diagnostic = {
        "resolver_version": "canonical-liquidity-resolver-1.0.0",
        "controlled_revision_count": len(controlled_keys),
        "visible_selected_count": visible,
        "future_revision_count": future,
        "deduplicated_source_count": visible,
        "retained_source_count": len(raw_rows),
        "issue_counts": {},
        "samples": [],
        "sample_limit": SAMPLE_LIMIT,
    }

    return effective, diagnostic