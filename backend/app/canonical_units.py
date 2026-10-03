"""Locked dimensional-unit contracts for canonical accounting evidence.

Rules in this module are deliberately exact.  They are not a general unit parser
and must never be replaced with plural stripping or fuzzy string matching.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

EPS_UNIT_RULE_ID = "eps-usd-per-share-lossless"
EPS_UNIT_RULE_VERSION = "1.0.0"
EPS_FIELDS = frozenset({"basic_eps", "diluted_eps"})
EPS_CONCEPTS = frozenset({"EarningsPerShareBasic", "EarningsPerShareDiluted"})


@dataclass(frozen=True)
class UnitNormalization:
    source_unit: str
    canonical_unit: str
    normalization_rule_identifier: str
    scale_factor: int
    rule_version: str
    reason: str

    def provenance(self) -> dict[str, Any]:
        return asdict(self)


def normalize_unit(*, canonical_field: str, source_unit: str, concept: str,
                   currency: str | None, scale_factor: int | float = 1,
                   period_nature: str = "duration") -> UnitNormalization | None:
    """Return the sole permitted lossless unit normalization, or ``None``.

    All predicates are equality checks so ``USD/shares`` is accepted only as the
    SEC spelling of exactly one USD-denominated share in an EPS duration fact.
    Other currencies, denominators, scaling, concepts and instant facts fail
    closed.
    """
    if (canonical_field not in EPS_FIELDS or source_unit != "USD/shares"
            or concept not in EPS_CONCEPTS or currency != "USD"
            or scale_factor != 1 or period_nature != "duration"):
        return None
    expected = "EarningsPerShareBasic" if canonical_field == "basic_eps" else "EarningsPerShareDiluted"
    if concept != expected:
        return None
    return UnitNormalization(
        source_unit=source_unit, canonical_unit="USD/share",
        normalization_rule_identifier=EPS_UNIT_RULE_ID, scale_factor=1,
        rule_version=EPS_UNIT_RULE_VERSION,
        reason="USD numerator and exactly-one-share denominator are dimensionally identical for the matched EPS concept; no currency conversion",
    )
