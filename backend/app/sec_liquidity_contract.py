"""Immutable identity for the controlled SEC liquidity operation."""
from __future__ import annotations

import hashlib
import json

from .liquidity_inventory import STANDARD_CONCEPTS

OPERATION_TYPE = "sec_liquidity_evidence_ingestion"
OPERATION_CONTRACT_VERSION = "1.0.0"
PARSER_VERSION = "sec-liquidity-exact-us-gaap-v1"
ENDPOINT_CLASSES = ("submissions", "companyfacts")
VALIDATION_RULES = (
    "exact-cik",
    "accepted-timestamp-required",
    "us-gaap-exact-concepts-only",
    "json-content-type",
    "sha256-original-payload",
)


def _contract_document() -> dict[str, object]:
    return {
        "concepts": sorted(STANDARD_CONCEPTS),
        "endpoint_classes": list(ENDPOINT_CLASSES),
        "parser_version": PARSER_VERSION,
        "validation_rules": list(VALIDATION_RULES),
    }


CONCEPT_CONTRACT_HASH = hashlib.sha256(
    json.dumps(_contract_document(), sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def operation_identity() -> dict[str, str]:
    return {
        "operation_type": OPERATION_TYPE,
        "operation_contract_version": OPERATION_CONTRACT_VERSION,
        "concept_contract_hash": CONCEPT_CONTRACT_HASH,
    }
