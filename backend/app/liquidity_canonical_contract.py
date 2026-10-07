"""Shared producer/consumer contract for canonical liquidity revisions."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any

OPERATION_TYPE = "liquidity_canonical_materialization"
OPERATION_CONTRACT_VERSION = "1.0.0"


def utc_datetime(value: Any) -> datetime | None:
    """Normalize a timezone-aware timestamp to UTC at microsecond precision."""
    if value is None:
        return None
    try:
        parsed=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def timestamp_text(value: Any) -> str | None:
    parsed=utc_datetime(value)
    return parsed.isoformat() if parsed else None


def date_value(value: Any) -> date | None:
    if value is None: return None
    if isinstance(value,datetime): return value.date()
    if isinstance(value,date): return value
    try: return date.fromisoformat(str(value))
    except (TypeError,ValueError): return None


def finite_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value,bool): return None
    try: result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError): return None
    return result if result.is_finite() else None


def same_number(left: Any,right: Any) -> bool:
    a,b=finite_decimal(left),finite_decimal(right)
    return a is not None and b is not None and a==b


def redundant_currency(value: Any,unit: Any) -> str | None:
    """SEC USD-unit rows may persist redundant currency as null, blank, or USD."""
    if str(unit)!="USD": return None
    text="" if value is None else str(value).strip().upper()
    return "USD" if text in {"","USD"} else None


def identity_scale(value: Any) -> Decimal | None:
    parsed=finite_decimal(value)
    if value is None: return Decimal(1)
    return Decimal(1) if parsed in {Decimal(0),Decimal(1)} else None


def source_key(row: dict[str,Any]) -> str | None:
    value=row.get("fact_key") or row.get("source_fact_key") or row.get("source_evidence_key")
    return str(value) if value not in (None,"") else None


def canonical_json(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()


def canonical_evidence_key(source_fact_key: str,canonical_field: str,decision_at: Any) -> str:
    decision=timestamp_text(decision_at)
    if not decision: raise ValueError("timezone-aware decision timestamp required")
    return hashlib.sha256(canonical_json([OPERATION_TYPE,source_fact_key,canonical_field,decision])).hexdigest()


def canonical_available_at(*,public_at: Any,retrieved_at: Any,materialized_at: Any) -> datetime:
    values=[utc_datetime(x) for x in (public_at,retrieved_at,materialized_at)]
    if any(x is None for x in values): raise ValueError("canonical timestamps invalid")
    return max(values)


def operation_identity(contract_hash: str,validator_version: str) -> dict[str,str]:
    return {"operation_type":OPERATION_TYPE,"operation_contract_version":OPERATION_CONTRACT_VERSION,
            "operation_contract_hash":contract_hash,"validator_version":validator_version}


def parse_object(value: Any) -> dict[str,Any] | None:
    if isinstance(value,dict): return value
    if not isinstance(value,str): return None
    try: parsed=json.loads(value)
    except (TypeError,ValueError,json.JSONDecodeError): return None
    return parsed if isinstance(parsed,dict) else None
