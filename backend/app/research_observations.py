"""Build a fail-closed, research-only cross-section from global market data.

The builder is deliberately a pure transformation: it accepts caller-provided
frames and neither opens DuckDB nor calls a provider.  Its output is suitable as
an input to the existing research feature/backtest code, not to the production
publisher.  Membership is the current catalogue supplied by the caller and must
not be described as historical or survivorship-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .eodhd_ingestion import PERMANENT_FAILURE_CODES
from .global_research import momentum_features


RESEARCH_LABEL = "RESEARCH ONLY — NOT INVESTMENT ADVICE"
EXPECTED_REGIONS = frozenset({"US", "LSE", "TO", "XETRA", "PA"})
REGION_CURRENCIES = {
    "US": frozenset({"USD"}),
    "LSE": frozenset({"GBP", "GBX"}),
    "TO": frozenset({"CAD"}),
    "XETRA": frozenset({"EUR"}),
    "PA": frozenset({"EUR"}),
}
class ObservationValidationError(ValueError):
    """A dataset-wide invariant failed, so no observations can be trusted."""


@dataclass(frozen=True)
class ObservationPolicy:
    minimum_price_sessions: int = 127
    maximum_price_age_days: int = 7
    maximum_fx_age_days: int = 7

    def __post_init__(self) -> None:
        if self.minimum_price_sessions < 127:
            raise ValueError("minimum_price_sessions must support 126-session momentum")
        if self.maximum_price_age_days < 0 or self.maximum_fx_age_days < 0:
            raise ValueError("freshness limits cannot be negative")


@dataclass(frozen=True)
class ModelReadyDataset:
    observations: pd.DataFrame
    report: dict[str, Any]


def _require_columns(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ObservationValidationError(
            f"Missing {label} columns: {', '.join(sorted(missing))}"
        )


def _utc(values: pd.Series, label: str) -> pd.Series:
    try:
        converted = pd.to_datetime(values, utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ObservationValidationError(f"Invalid {label} timestamp") from exc
    return converted


def _validate_inputs(
    catalogue: pd.DataFrame,
    prices: pd.DataFrame,
    fx: pd.DataFrame,
    actions: pd.DataFrame,
    failures: pd.DataFrame,
) -> None:
    _require_columns(
        catalogue,
        {"security_id", "qualified_symbol", "region", "currency", "eligible"},
        "catalogue",
    )
    _require_columns(
        prices,
        {
            "qualified_symbol", "trading_date", "currency", "open", "high",
            "low", "close", "adjusted_close", "volume", "status", "source",
            "retrieved_at",
        },
        "price",
    )
    _require_columns(
        fx,
        {"base_currency", "quote_currency", "observed_on", "rate", "available_at"},
        "FX",
    )
    _require_columns(
        actions,
        {"qualified_symbol", "ex_date", "action_type", "value"},
        "corporate action",
    )
    _require_columns(failures, {"qualified_symbol", "error_code"}, "failure")

    eligible = catalogue.loc[catalogue["eligible"].astype(bool)]
    regions = frozenset(eligible["region"].astype(str).str.upper())
    if regions != EXPECTED_REGIONS:
        raise ObservationValidationError(
            f"Eligible catalogue must contain exactly all five regions; got {sorted(regions)}"
        )
    if eligible["security_id"].duplicated().any() or eligible["qualified_symbol"].duplicated().any():
        raise ObservationValidationError("Duplicate eligible catalogue identity")
    invalid_currency = eligible.loc[
        eligible.apply(
            lambda row: str(row["currency"]).upper()
            not in REGION_CURRENCIES.get(str(row["region"]).upper(), frozenset()),
            axis=1,
        )
    ]
    if not invalid_currency.empty:
        raise ObservationValidationError("Catalogue currency does not match listing region")

    if prices.duplicated(["qualified_symbol", "trading_date"]).any():
        raise ObservationValidationError("Duplicate price observation")
    if fx.duplicated(["base_currency", "quote_currency", "observed_on"]).any():
        raise ObservationValidationError("Duplicate FX observation")
    if actions.duplicated(["qualified_symbol", "ex_date", "action_type"]).any():
        raise ObservationValidationError("Duplicate corporate action")
    if failures.duplicated(["qualified_symbol", "error_code"]).any():
        raise ObservationValidationError("Duplicate provider failure")


def build_model_ready_observations(
    *,
    catalogue: pd.DataFrame,
    prices: pd.DataFrame,
    fx: pd.DataFrame,
    actions: pd.DataFrame,
    failures: pd.DataFrame,
    decision_at: datetime,
    policy: ObservationPolicy = ObservationPolicy(),
) -> ModelReadyDataset:
    """Create one point-in-time observation per currently eligible security.

    Invalid security-level evidence produces an ineligible row with explicit
    reasons. Dataset-wide identity/key violations raise before any output is
    returned. This distinction prevents a partial, silently ambiguous dataset.
    """
    if decision_at.tzinfo is None:
        raise ObservationValidationError("decision_at must be timezone-aware")
    _validate_inputs(catalogue, prices, fx, actions, failures)
    boundary = pd.Timestamp(decision_at).tz_convert("UTC")

    price_frame = prices.copy()
    price_frame["trading_date"] = pd.to_datetime(price_frame["trading_date"]).dt.date
    price_frame["retrieved_at"] = _utc(price_frame["retrieved_at"], "price retrieval")
    fx_frame = fx.copy()
    fx_frame["observed_on"] = pd.to_datetime(fx_frame["observed_on"]).dt.date
    fx_frame["available_at"] = _utc(fx_frame["available_at"], "FX availability")
    action_frame = actions.copy()
    action_frame["ex_date"] = pd.to_datetime(action_frame["ex_date"]).dt.date

    rows: list[dict[str, Any]] = []
    eligible_catalogue = catalogue.loc[catalogue["eligible"].astype(bool)].copy()
    eligible_catalogue = eligible_catalogue.sort_values(["region", "qualified_symbol"])
    permanent = failures.loc[
        failures["error_code"].astype(str).str.lower().isin(PERMANENT_FAILURE_CODES)
    ]
    permanent_by_symbol = permanent.groupby("qualified_symbol")["error_code"].apply(list).to_dict()

    for item in eligible_catalogue.itertuples(index=False):
        symbol = str(item.qualified_symbol)
        currency = str(item.currency).upper()
        reasons: list[str] = []
        visible = price_frame.loc[
            (price_frame["qualified_symbol"] == symbol)
            & price_frame["retrieved_at"].le(boundary)
            & (price_frame["status"] == "available")
        ].sort_values("trading_date")
        if symbol in permanent_by_symbol:
            reasons.append("permanent_provider_failure")
        if visible.empty:
            reasons.append("missing_price_history")
        elif len(visible) < policy.minimum_price_sessions:
            reasons.append("insufficient_history")

        invalid_prices = False
        if not visible.empty:
            numeric = visible[["open", "high", "low", "close", "adjusted_close", "volume"]].apply(
                pd.to_numeric, errors="coerce"
            )
            invalid_prices = bool(
                numeric.isna().any().any()
                or (~np.isfinite(numeric.to_numpy(dtype=float))).any()
                or (numeric[["open", "high", "low", "close", "adjusted_close"]] <= 0).any().any()
                or (numeric["volume"] < 0).any()
                or (numeric["high"] < numeric[["open", "low", "close"]].max(axis=1)).any()
                or (numeric["low"] > numeric[["open", "high", "close"]].min(axis=1)).any()
                or visible["trading_date"].duplicated().any()
            )
            if invalid_prices:
                reasons.append("invalid_price_history")
            latest_date = visible["trading_date"].iloc[-1]
            if (boundary.date() - latest_date).days > policy.maximum_price_age_days:
                reasons.append("stale_price")
        else:
            latest_date = None

        local_adjusted = pd.to_numeric(visible["adjusted_close"], errors="coerce")
        gbp_values: list[float] = []
        if not visible.empty and not invalid_prices:
            for price_row, adjusted in zip(visible.itertuples(index=False), local_adjusted):
                if currency == "GBP":
                    gbp_values.append(float(adjusted))
                elif currency == "GBX":
                    gbp_values.append(float(adjusted) / 100.0)
                else:
                    available_fx = fx_frame.loc[
                        (fx_frame["base_currency"] == currency)
                        & (fx_frame["quote_currency"] == "GBP")
                        & fx_frame["available_at"].le(boundary)
                        & fx_frame["observed_on"].le(price_row.trading_date)
                    ].sort_values(["observed_on", "available_at"])
                    if available_fx.empty:
                        reasons.append("missing_fx")
                        break
                    fx_row = available_fx.iloc[-1]
                    rate = pd.to_numeric(pd.Series([fx_row["rate"]]), errors="coerce").iloc[0]
                    if not np.isfinite(rate) or rate <= 0:
                        reasons.append("invalid_fx")
                        break
                    if (price_row.trading_date - fx_row["observed_on"]).days > policy.maximum_fx_age_days:
                        reasons.append("stale_fx")
                        break
                    gbp_values.append(float(adjusted) * float(rate))

        symbol_actions = action_frame.loc[action_frame["qualified_symbol"] == symbol]
        if not symbol_actions.empty:
            action_values = pd.to_numeric(symbol_actions["value"], errors="coerce")
            if (
                ~symbol_actions["action_type"].isin({"split", "cash_distribution"})
            ).any() or action_values.isna().any() or (action_values <= 0).any():
                reasons.append("invalid_corporate_action")

        reasons = sorted(set(reasons))
        features = (
            momentum_features(local_adjusted, pd.Series(gbp_values))
            if not reasons and len(gbp_values) == len(visible)
            else {}
        )
        rows.append(
            {
                "security_id": str(item.security_id),
                "qualified_symbol": symbol,
                "region": str(item.region).upper(),
                "currency": currency,
                "decision_at": boundary,
                "latest_price_date": latest_date,
                "price_sessions": int(len(visible)),
                "corporate_action_count": int(len(symbol_actions)),
                "price_semantics": "provider_adjusted_close_total_return_input",
                "fx_semantics": "latest_available_on_or_before_price_date",
                "eligible": not reasons,
                "exclusion_reasons": reasons,
                **features,
            }
        )

    observations = pd.DataFrame(rows)
    reason_counts: dict[str, int] = {}
    for reasons in observations["exclusion_reasons"]:
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    usable = observations.loc[observations["eligible"]]
    report = {
        "label": RESEARCH_LABEL,
        "status": "ready" if not usable.empty else "withheld",
        "output": "model_ready_observations",
        "ranking_published": False,
        "membership_basis": "current_catalogue_not_survivorship_free",
        "decision_at": boundary.isoformat(),
        "catalogue_securities": int(len(observations)),
        "eligible_observations": int(len(usable)),
        "withheld_observations": int(len(observations) - len(usable)),
        "eligible_by_region": {
            region: int((usable["region"] == region).sum()) for region in sorted(EXPECTED_REGIONS)
        },
        "exclusions": dict(sorted(reason_counts.items())),
        "top_three_available": bool(len(usable) >= 3),
        "highest_conviction_available": False,
    }
    return ModelReadyDataset(observations.reset_index(drop=True), report)
